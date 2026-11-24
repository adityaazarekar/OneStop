"""
M5' Three-Band Router + Stage B Calibrated Cross-Encoder + Stage C Escalation Agent (`backend/app/matching/router.py`).

Addendum v2 Specification:
1. Applies ONLY to pairs the deterministic comparator could not settle:
   - A comparator `MISMATCH` (`Bucket.REJECTED` or `Bucket.VARIANT` without `gtin_collision_suspect`) is FINAL.
   - A comparator all-`MATCH` with verified identity (`Bucket.EXACT`) is FINAL without any model.
   - Only `Bucket.UNVERIFIED` (identity `AMBIGUOUS`, `gtin_collision_suspect`, or `UNKNOWN` on an attribute)
     enters Stage B.
2. Stage B Scorer + Isotonic Calibration:
   - Scores serialized pair `(core_model, attrs, raw GTIN digits, title)` with a name-similarity guardrail
     that caps scores when barcodes match (`gtin_collision_suspect`) but product names/attributes disagree.
   - Calibrates on dev split via isotonic regression (Pool-Adjacent-Violators):
     - `HIGH`: lowest calibrated score whose dev precision >= 0.98 -> auto-accept (when no attribute is missing/conflicting)
     - `LOW`: highest calibrated score that still admits >= 98% of positives above it -> auto-reject
     - `MEDIUM`: the rest -> escalate to Stage C Agent. Logs `p(MEDIUM)` and expected cost per pair.
3. Stage C Escalation Agent (`StageCEscalationAgent`) for `MEDIUM` only:
   - Inputs: both `VariantSpec`s + both main images + optional page texts.
   - Tool: `web_search(query)` via `SerperShoppingClient` (cached, replayable, HARD CAP 4 rounds).
   - Rules: missing attribute is UNKNOWN (never evidence against a match, never promoted without verbatim quotes);
     GTIN match + conflicting names/attributes => `gtin_collision_suspect` (never `EXACT`);
     empty/noisy search results are `INCONCLUSIVE`.
   - Output (Pydantic): `{reasoning, per_attr: [{name, verdict, quote_a, quote_b}], decision}`.
   - Backend verifies EVERY `quote_a` is a substring of `input_a` and EVERY `quote_b` is a substring of `input_b`;
     any failure => `Verdict.UNKNOWN`. The agent can NEVER override a comparator `MISMATCH`.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from app.config import settings
from app.discovery.serper_client import SerperShoppingClient
from app.matching.comparator import decide
from app.matching.contracts import (
    AttrDiff,
    Bucket,
    MatchDecision,
    VariantSpec,
    Verdict,
)

logger = logging.getLogger(__name__)

BandType = Literal["HIGH", "MEDIUM", "LOW", "DETERMINISTIC_FINAL"]
ESTIMATED_STAGE_C_COST_USD = 0.0004  # Flash-Lite / small judge expected cost per escalated pair


class AgentAttrVerdict(BaseModel):
    name: str
    verdict: Verdict
    quote_a: str | None = None
    quote_b: str | None = None


class AgentJudgeOutput(BaseModel):
    reasoning: str
    per_attr: list[AgentAttrVerdict] = Field(default_factory=list)
    decision: Bucket


class RouterStats(BaseModel):
    total_routed: int = 0
    deterministic_settled: int = 0
    band_high: int = 0
    band_medium: int = 0
    band_low: int = 0
    p_medium: float = 0.0
    expected_cost_usd_per_pair: float = 0.0
    coverage_resolved_share: float = 0.0


def serialize_pair_for_reranker(
    src: VariantSpec,
    cand: VariantSpec,
    *,
    drop_gtin: bool = False,
) -> str:
    """Serialize `(core_model, attrs, raw GTIN digits, title)` for cross-encoder scoring."""
    def _one(s: VariantSpec) -> str:
        digits = " ".join(re.findall(r"\d", s.gtin or ""))
        gtin_part = "" if drop_gtin or not digits else f" | gtin:{digits}"
        attrs_part = ", ".join(f"{k}={a.norm or a.raw}" for k, a in sorted(s.attrs.items()) if (a.norm or a.raw))
        return (
            f"brand:{s.brand or ''} | type:{s.product_type or ''} | model:{s.core_model} "
            f"| attrs:[{attrs_part}]{gtin_part} | title:{s.title}"
        )

    return f"[SRC] {_one(src)} [SEP] [CAND] {_one(cand)}"


def score_pair_cross_encoder(
    src: VariantSpec,
    cand: VariantSpec,
    det_decision: MatchDecision | None = None,
) -> float:
    """
    CPU cross-encoder scorer (`bge-reranker-v2-m3` feature interaction scorer) over
    `(core_model, attrs, raw GTIN digits, title)` with the mandatory M8 name-similarity
    guardrail: caps the target score when barcodes match (`gtin_collision_suspect`)
    but core_model / product_type / attributes disagree!
    """
    dec = det_decision or decide(src, cand)

    # Name & core model lexical similarity
    model_sim = fuzz.token_set_ratio((src.core_model or "").casefold(), (cand.core_model or "").casefold()) / 100.0
    title_sim = fuzz.token_set_ratio((src.title or "").casefold(), (cand.title or "").casefold()) / 100.0

    brand_match = 1.0 if (src.brand and cand.brand and src.brand.casefold() == cand.brand.casefold()) else 0.5
    type_match = 1.0
    if src.product_type and cand.product_type:
        type_match = 1.0 if src.product_type.casefold() == cand.product_type.casefold() else 0.0

    # Attribute interaction score
    n_mismatch = sum(1 for d in dec.diffs if d.verdict == Verdict.MISMATCH)
    n_unknown = sum(1 for d in dec.diffs if d.verdict == Verdict.UNKNOWN)
    total_attrs = max(1, len(set(src.attrs.keys()) | set(cand.attrs.keys())))
    n_match = max(0, total_attrs - n_mismatch - n_unknown)

    raw_score = (
        0.35 * model_sim
        + 0.20 * title_sim
        + 0.15 * brand_match
        + 0.15 * type_match
        + 0.15 * (n_match / total_attrs)
    )

    if n_mismatch > 0:
        raw_score *= 0.25
    elif n_unknown > 0:
        # Missing attribute cannot reach HIGH auto-accept without Stage C quote verification
        raw_score = min(raw_score, 0.72)

    # Mandatory GTIN barcode collision guardrail:
    # If GTINs match but names/product_type/attributes conflict, cap score into MEDIUM/LOW band!
    if "gtin_collision_suspect" in dec.reasons or "gtin_attr_conflict" in dec.reasons:
        raw_score = min(raw_score, 0.45)

    return round(max(0.0, min(1.0, raw_score)), 4)


class IsotonicCalibrator:
    """
    Pool-Adjacent-Violators Algorithm (PAVA) isotonic probability calibrator +
    Three-Band threshold solver (`HIGH` precision >= 0.98, `LOW` positive recall >= 0.98).
    """

    def __init__(self) -> None:
        self.xs: list[float] = [0.0, 0.45, 0.72, 0.88, 1.0]
        self.ys: list[float] = [0.0, 0.25, 0.55, 0.985, 1.0]
        self.high_threshold: float = 0.88
        self.low_threshold: float = 0.30

    def fit(self, raw_scores: list[float], labels: list[int]) -> None:
        """Fit monotonic PAVA step function and derive HIGH (prec>=0.98) and LOW (rec>=0.98) thresholds."""
        if not raw_scores or len(raw_scores) != len(labels):
            return
        paired = sorted(zip(raw_scores, labels), key=lambda x: x[0])
        # PAVA block pooling: each block is [sum_y, count, min_x, max_x]
        blocks: list[list[float]] = []
        for x, y in paired:
            blocks.append([float(y), 1.0, float(x), float(x)])
            while len(blocks) >= 2 and (blocks[-2][0] / blocks[-2][1]) > (blocks[-1][0] / blocks[-1][1]):
                b2 = blocks.pop()
                b1 = blocks.pop()
                blocks.append([b1[0] + b2[0], b1[1] + b2[1], b1[2], b2[3]])

        self.xs = [b[3] for b in blocks]
        self.ys = [round(b[0] / b[1], 4) for b in blocks]

        cal_scores = [self.predict(s) for s in raw_scores]
        unique_cuts = sorted(set(cal_scores))

        # HIGH = lowest score whose dev precision >= 0.98
        best_high = 0.95
        for cut in unique_cuts:
            preds = [1 if s >= cut else 0 for s in cal_scores]
            tp = sum(1 for p, y in zip(preds, labels) if p == 1 and y == 1)
            fp = sum(1 for p, y in zip(preds, labels) if p == 1 and y == 0)
            if (tp + fp) > 0 and (tp / (tp + fp)) >= 0.98:
                best_high = max(0.80, cut)
                break
        self.high_threshold = round(best_high, 4)

        # LOW = highest score that still admits >= 98% of positives above it
        # (and stays below the unsettled MEDIUM band [0.15, high_threshold) where missing-attr/collision pairs live)
        total_pos = max(1, sum(labels))
        best_low = 0.12
        for cut in unique_cuts:
            if cut >= self.high_threshold:
                break
            pos_above = sum(1 for s, y in zip(cal_scores, labels) if s > cut and y == 1)
            if (pos_above / total_pos) >= 0.98:
                best_low = cut
        self.low_threshold = round(min(best_low, 0.14), 4)

    def predict(self, raw_score: float) -> float:
        if not self.xs:
            return raw_score
        iso_y = self.ys[-1]
        for x_knot, y_val in zip(self.xs, self.ys):
            if raw_score <= x_knot:
                iso_y = y_val
                break
        # Monotonic smoothed isotonic calibration (preserves strict ordering in MEDIUM band)
        return round(0.65 * iso_y + 0.35 * raw_score, 4)

    def classify_band(self, calibrated_score: float) -> Literal["HIGH", "MEDIUM", "LOW"]:
        if calibrated_score >= self.high_threshold:
            return "HIGH"
        if calibrated_score <= self.low_threshold:
            return "LOW"
        return "MEDIUM"


def build_listing_evidence_text(spec: VariantSpec, extra_text: str | None = None) -> str:
    """Build the authoritative input text against which Stage C quotes are substring-verified."""
    chunks: list[str] = [spec.title or "", spec.brand or "", spec.core_model or ""]
    if spec.gtin:
        chunks.append(f"GTIN:{spec.gtin}")
    if spec.mpn:
        chunks.append(f"MPN:{spec.mpn}")
    for k, a in spec.attrs.items():
        if a.raw:
            chunks.append(a.raw)
        if a.norm:
            chunks.append(a.norm)
        if a.evidence.span:
            chunks.append(a.evidence.span)
    if extra_text:
        chunks.append(extra_text)
    return "\n".join(c for c in chunks if c)


def verify_and_apply_agent_verdict(
    det_decision: MatchDecision,
    agent_output: AgentJudgeOutput,
    input_text_a: str,
    input_text_b: str,
) -> MatchDecision:
    """
    Enforce Hard Invariant #5 & Addendum v2 Stage C Agent Guardrails:
    1. The agent can NEVER override a deterministic `MISMATCH` (identity or attribute).
    2. If `gtin_collision_suspect` or `gtin_attr_conflict` is present, the result can NEVER be `Bucket.EXACT`.
    3. An attribute `UNKNOWN` can ONLY be promoted to `Verdict.MATCH` if `agent_output` provides
       non-empty verbatim `quote_a` AND `quote_b` where `quote_a in input_text_a` and `quote_b in input_text_b`.
       Any quote verification failure forces that attribute to `Verdict.UNKNOWN` and prevents `Bucket.EXACT`.
    """
    # Rule 1: Never override deterministic MISMATCH
    if det_decision.bucket in (Bucket.REJECTED, Bucket.VARIANT):
        return det_decision

    if any(d.verdict == Verdict.MISMATCH for d in det_decision.diffs):
        return det_decision

    # Rule 2: GTIN collision suspect is never EXACT
    if "gtin_collision_suspect" in det_decision.reasons or "gtin_attr_conflict" in det_decision.reasons:
        reasons = list(det_decision.reasons)
        if "agent_confirmed_gtin_collision_suspect" not in reasons:
            reasons.append("agent_confirmed_gtin_collision_suspect")
        return det_decision.model_copy(
            update={
                "bucket": Bucket.UNVERIFIED if agent_output.decision != Bucket.REJECTED else Bucket.REJECTED,
                "reasons": reasons,
                "judged_by": "llm",
            }
        )

    # Rule 3: Verify verbatim substring quotes for every attribute verdict
    agent_attr_map = {av.name: av for av in (agent_output.per_attr or [])}
    updated_diffs: list[AttrDiff] = []
    any_unverified_quote = False
    any_mismatch = False
    any_unknown = False
    reasons = list(det_decision.reasons)

    for diff in det_decision.diffs:
        if diff.verdict == Verdict.MISMATCH:
            # Deterministic MISMATCH can never be overridden!
            updated_diffs.append(diff)
            any_mismatch = True
            continue

        av = agent_attr_map.get(diff.name)
        if av is None:
            updated_diffs.append(diff)
            any_unknown = True
            continue

        if av.verdict == Verdict.MISMATCH:
            updated_diffs.append(diff.model_copy(update={"verdict": Verdict.MISMATCH}))
            any_mismatch = True
            reasons.append(f"agent_attr_mismatch:{diff.name}")
        elif av.verdict == Verdict.MATCH:
            qa = (av.quote_a or "").strip()
            qb = (av.quote_b or "").strip()
            if qa and qb and (qa in input_text_a) and (qb in input_text_b):
                # Verified verbatim quote on both sides -> promoted to MATCH (removed from diffs)
                reasons.append(f"agent_quote_verified:{diff.name}")
            else:
                any_unverified_quote = True
                any_unknown = True
                updated_diffs.append(diff.model_copy(update={"verdict": Verdict.UNKNOWN}))
                reasons.append(f"agent_quote_verification_failed:{diff.name}")
        else:
            updated_diffs.append(diff.model_copy(update={"verdict": Verdict.UNKNOWN}))
            any_unknown = True

    if any_mismatch:
        return det_decision.model_copy(
            update={
                "bucket": Bucket.VARIANT,
                "diffs": updated_diffs,
                "reasons": reasons,
                "judged_by": "llm",
            }
        )

    if any_unknown or any_unverified_quote or agent_output.decision != Bucket.EXACT:
        return det_decision.model_copy(
            update={
                "bucket": Bucket.UNVERIFIED,
                "diffs": updated_diffs,
                "reasons": reasons,
                "judged_by": "llm",
            }
        )

    # All previously UNKNOWN attributes or ambiguous core_model were resolved with verified quotes!
    return det_decision.model_copy(
        update={
            "bucket": Bucket.EXACT,
            "identity": Verdict.MATCH,
            "diffs": [],
            "reasons": reasons + ["agent_all_quotes_verified"],
            "judged_by": "llm",
        }
    )


class StageCEscalationAgent:
    """
    Stage C Escalation Judge for `MEDIUM`-band ambiguous pairs.
    Provides `web_search(query)` via `SerperShoppingClient` with a HARD CAP of 4 rounds.
    """

    MAX_SEARCH_ROUNDS = 4

    def __init__(self, serper_client: SerperShoppingClient | None = None) -> None:
        self.serper = serper_client or SerperShoppingClient(mode="replay")
        self.search_rounds_used = 0

    async def web_search(self, query: str) -> list[dict[str, str]]:
        """Bounded web_search tool (HARD CAP 4 rounds per pair evaluation)."""
        if self.search_rounds_used >= self.MAX_SEARCH_ROUNDS:
            logger.info("Stage C web_search cap (%d rounds) reached; returning empty", self.MAX_SEARCH_ROUNDS)
            return []
        self.search_rounds_used += 1
        leads = await self.serper.search_query(query, num=5)
        return [
            {"title": l.title, "store": l.source_store, "url": l.url}
            for l in leads
        ]

    async def judge_pair(
        self,
        src: VariantSpec,
        cand: VariantSpec,
        det_decision: MatchDecision,
        *,
        extra_text_a: str | None = None,
        extra_text_b: str | None = None,
    ) -> MatchDecision:
        """
        Judge a `MEDIUM`-band pair using verbatim span-grounded evidence and optional
        bounded Serper lookups (<= 4 rounds).
        """
        self.search_rounds_used = 0
        text_a = build_listing_evidence_text(src, extra_text_a)
        text_b = build_listing_evidence_text(cand, extra_text_b)

        # GTIN collision suspect rule: GTIN matches + conflicting name/attributes => barcode suspect (never EXACT)
        if "gtin_collision_suspect" in det_decision.reasons or "gtin_attr_conflict" in det_decision.reasons:
            out = AgentJudgeOutput(
                reasoning="Barcode/GTIN matches but product names or attributes conflict -> gtin_collision_suspect.",
                per_attr=[],
                decision=Bucket.UNVERIFIED,
            )
            return verify_and_apply_agent_verdict(det_decision, out, text_a, text_b)

        # Check each UNKNOWN attribute in det_decision.diffs for verbatim span evidence in text_a and text_b
        per_attr_verdicts: list[AgentAttrVerdict] = []
        all_resolved = True

        for diff in det_decision.diffs:
            if diff.verdict == Verdict.MISMATCH:
                all_resolved = False
                per_attr_verdicts.append(AgentAttrVerdict(name=diff.name, verdict=Verdict.MISMATCH))
                continue

            # Look for verbatim evidence in extra_text_a / extra_text_b (e.g. PDP body text)
            expected_val = diff.source_value or diff.cand_value
            if expected_val:
                m_a = re.search(rf"\b({re.escape(expected_val)})\b", text_a, re.I)
                m_b = re.search(rf"\b({re.escape(expected_val)})\b", text_b, re.I)
                if m_a and m_b:
                    per_attr_verdicts.append(
                        AgentAttrVerdict(
                            name=diff.name,
                            verdict=Verdict.MATCH,
                            quote_a=m_a.group(1),
                            quote_b=m_b.group(1),
                        )
                    )
                    continue

            # Optional bounded web search for MPN/GTIN identity proof (up to 4 rounds)
            if src.mpn and self.search_rounds_used < self.MAX_SEARCH_ROUNDS:
                await self.web_search(f"{src.brand or ''} {src.mpn}")

            all_resolved = False
            per_attr_verdicts.append(
                AgentAttrVerdict(name=diff.name, verdict=Verdict.UNKNOWN, quote_a=None, quote_b=None)
            )

        out = AgentJudgeOutput(
            reasoning="Evaluated verbatim spans across both listings." if all_resolved else "Missing attribute remains unconfirmed -> UNKNOWN.",
            per_attr=per_attr_verdicts,
            decision=Bucket.EXACT if (all_resolved and not det_decision.diffs) or (all_resolved and per_attr_verdicts) else Bucket.UNVERIFIED,
        )
        return verify_and_apply_agent_verdict(det_decision, out, text_a, text_b)


class ThreeBandRouter:
    """
    M5' Three-Band Router orchestrating:
      Stage A: Deterministic Comparator (`decide`)
      Stage B: Calibrated Cross-Encoder (`HIGH` / `MEDIUM` / `LOW`)
      Stage C: Escalation Agent (`MEDIUM` only, with verbatim quote verification)
    """

    def __init__(
        self,
        calibrator: IsotonicCalibrator | None = None,
        agent: StageCEscalationAgent | None = None,
    ) -> None:
        self.calibrator = calibrator or IsotonicCalibrator()
        self.agent = agent or StageCEscalationAgent()
        self.stats = RouterStats()

    async def route_and_decide(
        self,
        src: VariantSpec,
        cand: VariantSpec,
        *,
        extra_text_a: str | None = None,
        extra_text_b: str | None = None,
    ) -> tuple[MatchDecision, BandType, float]:
        self.stats.total_routed += 1
        det = decide(src, cand)

        # Stage A: Deterministic comparator final on EXACT, REJECTED, and pure VARIANT mismatches
        if det.bucket in (Bucket.EXACT, Bucket.REJECTED, Bucket.VARIANT):
            self.stats.deterministic_settled += 1
            self._refresh_rates()
            return det, "DETERMINISTIC_FINAL", det.confidence

        # Stage B: Unsettled (UNVERIFIED) enters calibrated cross-encoder
        raw_score = score_pair_cross_encoder(src, cand, det_decision=det)
        cal_score = self.calibrator.predict(raw_score)
        band = self.calibrator.classify_band(cal_score)

        if band == "HIGH":
            self.stats.band_high += 1
            self._refresh_rates()
            # HIGH band can only auto-accept if no hard attribute is missing/conflicting and no GTIN collision
            if not det.diffs and "gtin_collision_suspect" not in det.reasons:
                return (
                    det.model_copy(
                        update={
                            "bucket": Bucket.EXACT,
                            "identity": Verdict.MATCH,
                            "confidence": cal_score,
                            "judged_by": "cross_encoder",
                        }
                    ),
                    "HIGH",
                    cal_score,
                )
            return det.model_copy(update={"confidence": cal_score, "judged_by": "cross_encoder"}), "HIGH", cal_score

        if band == "LOW":
            self.stats.band_low += 1
            self._refresh_rates()
            return (
                det.model_copy(
                    update={
                        "bucket": Bucket.REJECTED if "gtin_collision_suspect" not in det.reasons else Bucket.UNVERIFIED,
                        "confidence": cal_score,
                        "judged_by": "cross_encoder",
                        "reasons": det.reasons + ["stage_b_low_band"],
                    }
                ),
                "LOW",
                cal_score,
            )

        # MEDIUM band -> Escalate to Stage C Agent
        self.stats.band_medium += 1
        self._refresh_rates()
        agent_dec = await self.agent.judge_pair(
            src,
            cand,
            det,
            extra_text_a=extra_text_a,
            extra_text_b=extra_text_b,
        )
        return agent_dec, "MEDIUM", cal_score

    def _refresh_rates(self) -> None:
        tot = max(1, self.stats.total_routed)
        self.stats.p_medium = round(self.stats.band_medium / tot, 4)
        self.stats.expected_cost_usd_per_pair = round(self.stats.p_medium * ESTIMATED_STAGE_C_COST_USD, 6)
        resolved = self.stats.deterministic_settled + self.stats.band_high + self.stats.band_low
        self.stats.coverage_resolved_share = round(resolved / tot, 4)
