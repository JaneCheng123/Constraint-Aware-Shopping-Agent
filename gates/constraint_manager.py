import json
import copy
import re

from agent.llm_client import LLMClient
from gates.hard_constraints import parse_price_constraint


class ConstraintManager:

    STOPWORDS = {
        "a", "an", "the", "for", "with", "of", "to",
        "in", "on", "and", "or",
    }

    VALID_MATCH_STATUS = {
        "SUPPORTED",
        "MISSING",
        "CONTRADICTED",
    }

    def __init__(self, model=None, client=None):
        self.client = client or LLMClient(model=model)
        self.model = model or self.client.model

        self._schema_cache = {}

        # Same evidence + same product-type constraint should
        # not be re-judged differently by the LLM.
        self._product_type_semantic_cache = {}

        self.extraction_calls = 0
        self.validation_calls = 0
        self.schema_cache_hits = 0

        self.semantic_match_calls = 0
        self.semantic_match_errors = 0

        # Product-type semantic classification is tracked
        # separately because product category semantics differ
        # from ordinary attribute semantics.
        self.product_type_semantic_calls = 0
        self.product_type_semantic_errors = 0
        self.product_type_semantic_cache_hits = 0

    # Backward-compatible name used by earlier code.
    @property
    def extraction_cache_hits(self):
        return self.schema_cache_hits

    # =========================================================
    # Generic helpers
    # =========================================================

    @staticmethod
    def _extract_json(text):

        if not text:
            return None

        text = str(text).strip()

        text = re.sub(
            r"^```(?:json)?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )

        text = re.sub(
            r"\s*```$",
            "",
            text,
        )

        try:
            return json.loads(text)
        except Exception:
            pass

        match = re.search(
            r"\{.*\}",
            text,
            flags=re.DOTALL,
        )

        if not match:
            return None

        try:
            return json.loads(
                match.group(0)
            )
        except Exception:
            return None

    @staticmethod
    def normalize_text(text):

        text = str(
            text or ""
        ).lower()

        text = re.sub(
            r"[^a-z0-9]+",
            " ",
            text,
        )

        return re.sub(
            r"\s+",
            " ",
            text,
        ).strip()

    @classmethod
    def _simple_stem(cls, token):

        token = str(token)

        if len(token) > 4 and token.endswith("ies"):
            return token[:-3] + "y"

        if (
            len(token) > 3
            and token.endswith("s")
            and not token.endswith("ss")
        ):
            return token[:-1]

        return token

    @classmethod
    def content_tokens(cls, text):

        normalized = cls.normalize_text(
            text
        )

        result = []

        for token in normalized.split():

            if token in cls.STOPWORDS:
                continue

            result.append(
                cls._simple_stem(
                    token
                )
            )

        return result

    # =========================================================
    # Schema normalization
    # =========================================================

    @classmethod
    def _normalize_item(cls, value):

        if isinstance(value, str):
            value = {
                "source_text": value,
                "canonical": value,
                "aliases": [],
            }

        if not isinstance(value, dict):
            value = {}

        source_text = str(
            value.get(
                "source_text",
                value.get(
                    "text",
                    "",
                ),
            )
        ).strip()

        canonical = str(
            value.get(
                "canonical",
                value.get(
                    "text",
                    source_text,
                ),
            )
        ).strip()

        aliases = value.get(
            "aliases",
            [],
        )

        if not isinstance(aliases, list):
            aliases = []

        clean_aliases = []
        seen = set()

        for alias in aliases[:3]:

            alias = str(alias).strip()
            norm = cls.normalize_text(alias)

            if (
                not alias
                or not norm
                or norm in seen
            ):
                continue

            seen.add(norm)
            clean_aliases.append(alias)

        return {
            "source_text": source_text,
            "canonical": canonical,
            "aliases": clean_aliases,
            "kind": str(value.get("kind", "attribute")).lower(),
            "value": str(value.get("value", canonical)).strip(),
        }

    @classmethod
    def _normalize_schema(cls, schema):

        if not isinstance(schema, dict):
            schema = {}

        product_type = cls._normalize_item(
            schema.get(
                "product_type",
                {},
            )
        )
        product_type["kind"] = "product_type"

        required_raw = schema.get(
            "required_constraints",
            [],
        )

        post_raw = schema.get(
            "post_selection_constraints",
            [],
        )

        if not isinstance(required_raw, list):
            required_raw = []

        if not isinstance(post_raw, list):
            post_raw = []

        required = []
        seen = set()

        for value in required_raw:

            item = cls._normalize_item(value)

            key = cls.normalize_text(
                item["source_text"]
                or item["canonical"]
            )

            if not key or key in seen:
                continue

            seen.add(key)
            required.append(item)

        post = []
        seen = set()

        for value in post_raw:

            item = cls._normalize_item(value)

            key = cls.normalize_text(
                item["source_text"]
                or item["canonical"]
            )

            if not key or key in seen:
                continue

            seen.add(key)
            post.append(item)

        price_constraint = schema.get(
            "price_constraint"
        )

        if isinstance(price_constraint, dict):
            price_constraint = price_constraint.get("source_text") or price_constraint.get("canonical") or price_constraint.get("value")

        if price_constraint is not None:

            price_constraint = str(
                price_constraint
            ).strip()

            if not price_constraint:
                price_constraint = None

        return {
            "product_type": product_type,
            "required_constraints": required,
            "post_selection_constraints": post,
            "price_constraint": price_constraint,
        }

    # =========================================================
    # Extraction sanity check
    # =========================================================

    @classmethod
    def _source_is_anchored(
        cls,
        instruction,
        source_text,
    ):

        instruction_norm = cls.normalize_text(
            instruction
        )

        source_norm = cls.normalize_text(
            source_text
        )

        if not source_norm:
            return False

        return (
            f" {source_norm} "
            in f" {instruction_norm} "
        )

    @classmethod
    def _deterministic_validation_issues(
        cls,
        instruction,
        schema,
    ):

        issues = []

        product_type = schema.get(
            "product_type",
            {},
        )

        if not product_type.get("source_text"):
            issues.append(
                "product_type has no source_text"
            )

        elif not cls._source_is_anchored(
            instruction,
            product_type["source_text"],
        ):
            issues.append(
                "product_type source_text is not anchored "
                "in the original instruction"
            )

        for group_name in [
            "required_constraints",
            "post_selection_constraints",
        ]:

            for index, item in enumerate(
                schema.get(
                    group_name,
                    [],
                )
            ):

                if not item.get("source_text"):

                    issues.append(
                        f"{group_name}[{index}] "
                        "has no source_text"
                    )

                elif not cls._source_is_anchored(
                    instruction,
                    item["source_text"],
                ):

                    issues.append(
                        f"{group_name}[{index}] "
                        "source_text is not anchored "
                        "in the original instruction"
                    )

                if not item.get("canonical"):

                    issues.append(
                        f"{group_name}[{index}] "
                        "has no canonical form"
                    )

        for option in schema.get("post_selection_constraints", []):
            if option.get("kind") not in {"color", "size", "style", "scent", "pack", "count", "variant", "flavor"}:
                issues.append("Selectable option needs an explicit option-group kind")
            if not option.get("value"):
                issues.append("Selectable option needs an exact value")
        expected_price = parse_price_constraint(instruction)
        actual_price = parse_price_constraint(schema.get("price_constraint"))
        if expected_price != actual_price or (schema.get("price_constraint") and actual_price is None):
            issues.append("Price bounds were omitted or changed")
        return issues

    def validate_extraction(
        self,
        instruction,
        schema,
        use_llm=True,
    ):

        instruction = str(
            instruction
        ).strip()

        normalized = self._normalize_schema(
            schema
        )

        deterministic_issues = (
            self._deterministic_validation_issues(
                instruction,
                normalized,
            )
        )

        if not use_llm:

            return {
                "valid": (
                    len(deterministic_issues)
                    == 0
                ),
                "schema": normalized,
                "issues": deterministic_issues,
                "used_llm": False,
                "corrected": False,
                "error": None,
            }

        prompt = f"""
You are validating a structured constraint extraction
for a shopping agent.

Original user instruction:
{instruction}

Proposed extraction:
{json.dumps(normalized, ensure_ascii=False)}

Check the extraction carefully. valid must describe the corrected_schema you return; return boolean false if that final schema cannot be verified. Check kind and value of every selectable option, including color and size, as well as price completeness.
The normalized schema permits kind and value metadata on each constraint item, including
product_type (kind=product_type). Do not reject or remove valid metadata merely
because the illustrative output below omits it. Check meaning, not optional fields.
price_constraint is a plain string copied from the original price phrase, or null.
It does NOT need an object, kind, value or aliases. A string is the correct format.

CRITICAL RULES:

1. source_text must correspond to wording explicitly present
   in the original instruction.

2. canonical must preserve EXACTLY the same semantic direction
   as source_text.

3. aliases must preserve the SAME meaning and polarity.

4. Do not invent constraints.

5. Do not omit explicit retrieval-relevant constraints.

6. Product type must represent the requested product category.

6a. Product-type aliases must represent the SAME product
    category, not a related product, substitute, accessory,
    or neighboring category.

7. Exact selectable options such as color / size / pack count
   may belong in post_selection_constraints.

8. Preserve polarity.

Examples:

"animal testing"
must NOT become:
"cruelty free" or "not tested on animals".

"with alcohol"
must NOT become:
"alcohol free".

"contains fragrance"
must NOT become:
"fragrance free".

"cruelty free"
may use alias:
"not tested on animals".

If anything is wrong, return a corrected schema.

Return ONLY JSON:

{{
  "valid": true,
  "issues": [],
  "corrected_schema": {{
    "product_type": {{
      "source_text": "exact wording from instruction",
      "canonical": "normalized same-meaning form",
      "aliases": []
    }},
    "required_constraints": [],
    "post_selection_constraints": [],
    "price_constraint": null
  }}
}}
"""

        try:

            self.validation_calls += 1

            response = self.client.create(
                purpose="validate_extraction",
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                temperature=0,
            )

            raw_output = (
                response["choices"][0]["message"]
                ["content"]
                .strip()
            )

            parsed = self._extract_json(
                raw_output
            )

            if not isinstance(parsed, dict):
                raise ValueError(
                    "Extraction validator returned "
                    "invalid JSON."
                )

            llm_valid = parsed.get("valid") is True

            issues = parsed.get(
                "issues",
                [],
            )

            if not isinstance(issues, list):
                issues = []

            issues = [
                str(x).strip()
                for x in issues
                if str(x).strip()
            ]

            corrected = parsed.get(
                "corrected_schema",
                normalized,
            )

            corrected = self._normalize_schema(
                corrected
            )

            final_issues = (
                self._deterministic_validation_issues(
                    instruction,
                    corrected,
                )
            )

            all_issues = (
                deterministic_issues
                + issues
                + final_issues
            )

            # Deduplicate diagnostics.
            deduped = []

            for issue in all_issues:
                if issue not in deduped:
                    deduped.append(issue)

            # Require affirmative semantic approval of the returned schema.
            final_valid = (llm_valid and len(final_issues) == 0)

            return {
                "valid": final_valid,
                "schema": corrected,
                "issues": deduped,
                "used_llm": True,
                "corrected": (
                    not llm_valid
                    or corrected != normalized
                ),
                "raw_output": raw_output,
                "error": None,
            }

        except Exception as e:

            # A validator outage never supplies semantic approval.
            return {
                "valid": False,
                "schema": normalized,
                "issues": (
                    deterministic_issues
                    + [
                        "LLM validation error: "
                        + str(e)
                    ]
                ),
                "used_llm": True,
                "corrected": False,
                "raw_output": None,
                "error": str(e),
            }

    # =========================================================
    # Instruction extraction
    # =========================================================

    def extract_from_instruction(
        self,
        instruction,
    ):

        instruction = str(
            instruction
        ).strip()

        if instruction in self._schema_cache:

            self.schema_cache_hits += 1

            return copy.deepcopy(self._schema_cache[
                instruction
            ])

        prompt = f"""
You are extracting shopping constraints from a user instruction.

User instruction:
{instruction}

Extract ONLY explicitly requested information. Include every explicit price, color, size, brand and category requirement. For each selectable option include kind (color/size/style/scent/pack) and value (exact selectable value). For fixed brand constraints use kind=brand and value=brand name. price_constraint is the original price phrase, preserving decimals and strict/inclusive bounds.
Return price_constraint as a plain JSON string (e.g. "under $10"), or null when no
price is requested. Do not put an object or kind/value metadata in this field.

IMPORTANT:
source_text MUST be copied from the original user instruction
as closely and literally as possible.

canonical may normalize singular/plural or wording, but MUST
preserve exactly the same meaning and semantic polarity.

aliases may contain up to 3 conservative same-meaning phrases.

For PRODUCT TYPE aliases specifically:
- aliases must denote the same product category;
- do not add related products;
- do not add substitutes;
- do not add neighboring product categories.

Do not invent constraints.
Do not reverse constraints.

Examples:

"animal testing"
must stay semantically "animal testing".
Do NOT convert it to "cruelty free".

"with alcohol"
must NOT become "alcohol free".

"easy clean"
may canonicalize to "easy clean"
and alias to "easy to clean".

Return ONLY JSON:

{{
  "product_type": {{
    "source_text": "exact product wording from instruction",
    "canonical": "normalized product type",
    "aliases": []
  }},
  "required_constraints": [
    {{
      "source_text": "exact constraint wording",
      "canonical": "same-meaning normalized form",
      "aliases": [],
      "kind": "attribute",
      "value": "explicit value"
    }}
  ],
  "post_selection_constraints": [
    {{
      "source_text": "exact option wording",
      "canonical": "same-meaning normalized form",
      "aliases": [],
      "kind": "color",
      "value": "exact selectable option value"
    }}
  ],
  "price_constraint": null
}}
"""

        try:

            self.extraction_calls += 1

            response = self.client.create(
                purpose="extract_from_instruction",
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                temperature=0,
            )

            raw_output = (
                response["choices"][0]["message"]
                ["content"]
                .strip()
            )

            parsed = self._extract_json(
                raw_output
            )

            if not isinstance(parsed, dict):
                raise ValueError(
                    "Constraint extractor returned "
                    "invalid JSON."
                )

            validation = self.validate_extraction(
                instruction=instruction,
                schema=parsed,
                use_llm=True,
            )

            schema = validation["schema"]

            schema["validation"] = {
                "valid": validation["valid"],
                "issues": validation["issues"],
                "corrected": validation[
                    "corrected"
                ],
                "used_llm": validation[
                    "used_llm"
                ],
            }

            schema["raw_extraction_output"] = (
                raw_output
            )

            if validation["valid"]:
                schema["extraction_error"] = False
            else:
                schema["extraction_error"] = (
                    "Constraint extraction failed "
                    "validation."
                )

        except Exception as e:

            schema = {
                "product_type": {
                    "source_text": "",
                    "canonical": "",
                    "aliases": [],
                },
                "required_constraints": [],
                "post_selection_constraints": [],
                "price_constraint": None,
                "validation": {
                    "valid": False,
                    "issues": [
                        str(e)
                    ],
                    "corrected": False,
                    "used_llm": False,
                },
                "raw_extraction_output": None,
                "extraction_error": str(e),
            }

        self._schema_cache[
            instruction
        ] = copy.deepcopy(schema)

        return schema

    def get_constraints(
        self,
        instruction,
    ):
        return self.extract_from_instruction(
            instruction
        )

    # =========================================================
    # Matching cascade
    # =========================================================

    @classmethod
    def _base_candidates(
        cls,
        constraint,
    ):

        values = []

        for key in [
            "source_text",
            "canonical",
        ]:

            value = str(
                constraint.get(
                    key,
                    "",
                )
            ).strip()

            if value:
                values.append(value)

        return values

    @classmethod
    def exact_match(
        cls,
        text,
        constraint,
    ):

        text_norm = cls.normalize_text(
            text
        )

        for phrase in cls._base_candidates(
            constraint
        ):

            phrase_norm = cls.normalize_text(
                phrase
            )

            if (
                phrase_norm
                and not re.search(r"\b(?:not|no|without|never)\s+(?:\w+\s+){0,2}" + re.escape(phrase_norm) + r"\b", text_norm)
                and (
                    f" {phrase_norm} "
                    in f" {text_norm} "
                )
            ):

                return True, phrase

        return False, None

    @classmethod
    def alias_match(
        cls,
        text,
        constraint,
    ):

        text_norm = cls.normalize_text(
            text
        )

        for alias in constraint.get(
            "aliases",
            [],
        ):

            alias_norm = cls.normalize_text(
                alias
            )

            if (
                alias_norm
                and not re.search(r"\b(?:not|no|without|never)\s+(?:\w+\s+){0,2}" + re.escape(alias_norm) + r"\b", text_norm)
                and (
                    f" {alias_norm} "
                    in f" {text_norm} "
                )
            ):

                return True, alias

        return False, None

    @classmethod
    def token_match(
        cls,
        text,
        constraint,
    ):

        text_tokens = set(
            cls.content_tokens(
                text
            )
        )

        candidates = (
            cls._base_candidates(
                constraint
            )
            + list(
                constraint.get(
                    "aliases",
                    [],
                )
            )
        )

        for phrase in candidates:

            phrase_tokens = (
                cls.content_tokens(
                    phrase
                )
            )

            if not phrase_tokens:
                continue

            if set(
                phrase_tokens
            ).issubset(
                text_tokens
            ):

                return True, phrase

        return False, None

    # =========================================================
    # Semantic fallback
    # =========================================================

    # =========================================================
    # Specialized PRODUCT TYPE semantic matching
    #
    # Product type has different semantics from an attribute:
    #
    # easy clean vs no clean information
    #     -> MISSING
    #
    # tongue cleaner vs lipstick
    #     -> CONTRADICTED
    #
    # Therefore product type needs a dedicated 3-way judge.
    # =========================================================

    def semantic_match_product_type(
        self,
        text,
        constraint,
        context="product type",
    ):

        # -------------------------------------------------
        # PRODUCT-TYPE SEMANTIC CACHE
        #
        # Same accumulated evidence should always receive the
        # same semantic product-type decision during one task.
        # -------------------------------------------------

        cache_key = (
            self.normalize_text(
                text
            ),
            json.dumps(
                constraint,
                ensure_ascii=False,
                sort_keys=True,
            ),
        )

        if (
            cache_key
            in self._product_type_semantic_cache
        ):

            self.product_type_semantic_cache_hits += 1

            return dict(
                self._product_type_semantic_cache[
                    cache_key
                ]
            )

        prompt = f"""
You are resolving the PRODUCT TYPE of a visible shopping product.

Context:
{context}

Requested product type:
{json.dumps(constraint, ensure_ascii=False)}

Visible candidate product evidence:
{text}


============================================================
YOUR TASK
============================================================

Classify whether the visible candidate represents the SAME
product category requested by the user.

This is specifically a PRODUCT CATEGORY judgment, not an
ordinary product attribute judgment.


============================================================
STATUSES
============================================================

SUPPORTED

Use SUPPORTED when the visible product is clearly the same
product category or a genuine same-category synonym.

Examples:

requested:
tongue cleaner

visible:
tongue scraper

=> SUPPORTED


requested:
men's fragrance

visible:
men's eau de parfum

=> SUPPORTED


============================================================

CONTRADICTED

Use CONTRADICTED when the visible evidence clearly identifies
the candidate as a DIFFERENT product category.

Examples:

requested:
tongue cleaner

visible:
matte lipstick

=> CONTRADICTED


requested:
refillable container

visible:
wireless headphones

=> CONTRADICTED


requested:
hair loss treatment

visible:
phone case

=> CONTRADICTED


IMPORTANT:

A clearly different product category is CONTRADICTED,
not MISSING.


============================================================

MISSING

Use MISSING only when the visible evidence is too vague to
identify the product category.

Example:

requested:
tongue cleaner

visible:
premium personal care tool

=> MISSING


============================================================
STRICT RULES
============================================================

Use ONLY the visible text.

Do NOT use:
- target ASIN
- hidden attributes
- reward
- external product knowledge

Preserve the requested category semantics.

IMPORTANT FOR BROAD / UMBRELLA CATEGORIES:

Some requested product types explicitly include broad category
words such as:

- accessories
- accessory
- supplies
- tools
- treatments
- care
- kits
- equipment

When the USER'S REQUEST itself explicitly contains such a broad
category, a concrete product that clearly belongs inside that
domain may be SUPPORTED even if its title uses a more specific
functional name.

Examples:

requested:
hair extensions, wigs & accessories

visible:
storage / carrying bag designed for wigs and hair extensions

=> SUPPORTED


requested:
hair extensions, wigs & accessories for hair extensions

visible:
double-sided adhesive tape specifically for hair extensions

=> SUPPORTED


requested:
foot, hand & nail care

visible:
dermatologist-tested healing ointment intended for dry or
cracked skin on hands / feet

=> SUPPORTED


However:

requested:
tongue cleaner

visible:
lipstick

=> CONTRADICTED


A related product is NOT automatically supported when the
requested category is narrow.

For a BROAD requested category, if category membership is
plausible but the visible evidence is not sufficient, prefer
MISSING rather than CONTRADICTED.

Use CONTRADICTED only when the visible product is clearly outside
the requested category or domain.


Return ONLY valid JSON:

{{
  "status": "SUPPORTED or MISSING or CONTRADICTED",
  "evidence": "short visible evidence or empty string",
  "reason": "short explanation"
}}
"""

        try:

            self.product_type_semantic_calls += 1

            response = self.client.create(
                purpose="semantic_match_product_type",
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                temperature=0,
            )

            raw_output = (
                response["choices"][0]["message"]
                ["content"]
                .strip()
            )

            parsed = self._extract_json(
                raw_output
            )

            if not isinstance(parsed, dict):

                raise ValueError(
                    "semantic_match_product_type "
                    "returned invalid JSON"
                )


            status = str(
                parsed.get(
                    "status",
                    "MISSING",
                )
            ).strip().upper()


            if status not in self.VALID_MATCH_STATUS:

                status = "MISSING"


            result = {
                "status": status, "match_type": "product_type_semantic",
                "matched_by": str(parsed.get("evidence", "")).strip() or None,
                "reason": str(parsed.get("reason", "")).strip(),
                "raw_output": raw_output, "error": None,
            }

            self._product_type_semantic_cache[
                cache_key
            ] = dict(
                result
            )

            return result


        except Exception as e:

            self.product_type_semantic_errors += 1

            return {
                "status": "MISSING",
                "match_type": (
                    "product_type_semantic_error"
                ),
                "matched_by": None,
                "reason": (
                    "Product-type semantic "
                    "classification failed."
                ),
                "raw_output": None,
                "error": str(e),
            }


    def match_product_type(
        self,
        text,
        constraint,
        use_semantic=True,
        context="product type",
    ):
        """
        Product-type cascade:

        exact
          -> alias
          -> token
          -> specialized product-type semantic judge

        Unlike ordinary match_constraint(), the semantic fallback
        may classify a clearly different category as CONTRADICTED.
        """

        matched, phrase = self.exact_match(
            text,
            constraint,
        )

        if matched:

            return {
                "status": "SUPPORTED",
                "match_type": "exact",
                "matched_by": phrase,
                "reason": (
                    "exact normalized product-type match"
                ),
                "error": None,
            }


        matched, phrase = self.alias_match(
            text,
            constraint,
        )

        if matched:

            return {
                "status": "SUPPORTED",
                "match_type": "alias",
                "matched_by": phrase,
                "reason": (
                    "same-category product alias match"
                ),
                "error": None,
            }


        if use_semantic:

            return (
                self.semantic_match_product_type(
                    text=text,
                    constraint=constraint,
                    context=context,
                )
            )


        return {
            "status": "MISSING",
            "match_type": "none",
            "matched_by": None,
            "reason": (
                "No deterministic product-type match."
            ),
            "error": None,
        }


    def semantic_match(
        self,
        text,
        constraint,
        context="generic",
    ):

        prompt = f"""
You are resolving one uncertain shopping-constraint match.

Context:
{context}

Constraint:
{json.dumps(constraint, ensure_ascii=False)}

Candidate text:
{text}

Determine whether the candidate text supports the SAME
constraint in the SAME semantic direction.

Do not use outside knowledge.
Do not invent properties.
Do not reverse polarity.

Statuses:

SUPPORTED
- candidate text clearly expresses the same requirement.

CONTRADICTED
- candidate text clearly expresses the opposite or conflicts.

MISSING
- evidence is insufficient or unrelated.

Examples:

constraint = "cruelty free"
candidate = "not tested on animals"
=> SUPPORTED

constraint = "animal testing"
candidate = "not tested on animals"
=> CONTRADICTED

constraint = "easy clean"
candidate = "rinses clean quickly under running water"
=> SUPPORTED

Return ONLY JSON:

{{
  "status": "SUPPORTED or MISSING or CONTRADICTED",
  "evidence": "short matching text or empty string",
  "reason": "short explanation"
}}
"""

        try:

            self.semantic_match_calls += 1

            response = self.client.create(
                purpose="semantic_match",
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                temperature=0,
            )

            raw_output = (
                response["choices"][0]["message"]
                ["content"]
                .strip()
            )

            parsed = self._extract_json(
                raw_output
            )

            if not isinstance(parsed, dict):
                raise ValueError(
                    "semantic_match returned invalid JSON"
                )

            status = str(
                parsed.get(
                    "status",
                    "MISSING",
                )
            ).strip().upper()

            if status not in self.VALID_MATCH_STATUS:
                status = "MISSING"

            return {
                "status": status,
                "match_type": "semantic",
                "matched_by": str(
                    parsed.get(
                        "evidence",
                        "",
                    )
                ).strip() or None,
                "reason": str(
                    parsed.get(
                        "reason",
                        "",
                    )
                ).strip(),
                "raw_output": raw_output,
                "error": None,
            }

        except Exception as e:

            self.semantic_match_errors += 1

            return {
                "status": "MISSING",
                "match_type": "semantic_error",
                "matched_by": None,
                "reason": (
                    "Semantic fallback failed."
                ),
                "raw_output": None,
                "error": str(e),
            }

    def match_constraint(
        self,
        text,
        constraint,
        use_semantic=True,
        context="generic",
    ):

        matched, phrase = self.exact_match(
            text,
            constraint,
        )

        if matched:
            return {
                "status": "SUPPORTED",
                "match_type": "exact",
                "matched_by": phrase,
                "reason": "exact normalized phrase match",
                "error": None,
            }

        matched, phrase = self.alias_match(
            text,
            constraint,
        )

        if matched:
            return {
                "status": "SUPPORTED",
                "match_type": "alias",
                "matched_by": phrase,
                "reason": "same-meaning alias match",
                "error": None,
            }

        if use_semantic:
            return self.semantic_match(
                text=text,
                constraint=constraint,
                context=context,
            )

        return {
            "status": "MISSING",
            "match_type": "none",
            "matched_by": None,
            "reason": (
                "No deterministic match."
            ),
            "error": None,
        }
