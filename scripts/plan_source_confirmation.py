"""Validate and cost an offline confirmation proposal. Never launches an experiment."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def summarize(plan):
    require(plan["schema"] == "fli.source-confirmation.proposal.v1", "unknown proposal schema")
    require(plan["status"] == "proposal_not_frozen_not_launched", "proposal cannot authorize execution")
    require(plan["launch"]["authorized"] is False, "launch requires a separate approved frozen plan")
    require(plan["launch"]["modelCalls"] == plan["launch"]["cloudMutations"] == 0, "proposal must remain offline")
    source, questions, representation, answering = (
        plan["sourceTarget"],
        plan["questions"],
        plan["representation"],
        plan["answering"],
    )
    require(30 <= source["documents"] <= 50, "source target outside proposed range")
    require(sum(source["formats"].values()) == source["documents"], "format inventory differs")
    require(source["evidenceBearing"] + source["distractors"] == source["documents"], "source roles differ")
    require(
        source["domainFamilies"] * source["documentsPerDomainFamily"] == source["documents"], "domain inventory differs"
    )
    require(150 <= questions["fresh"] <= 200, "question target outside proposed range")
    require(questions["answerable"] + questions["missingAnswer"] == questions["fresh"], "question inventory differs")
    require(sum(questions["primaryStrata"].values()) == questions["fresh"], "primary strata must partition questions")
    require(sum(questions["wordingOverAnswerable"].values()) == questions["answerable"], "wording inventory differs")
    require(questions["freshQuestionTextAuthored"] is False, "this proposal must not expose confirmation questions")
    require(answering["primaryModels"] == 1 and answering["firstPassRepeats"] == 1, "primary matrix changed")
    repeat = answering["prospectiveSecondRepeatQuestions"]
    require(type(repeat) is int and 0 <= repeat <= questions["fresh"], "invalid repeat subset")
    require(
        representation["navigationArms"] == ["flat_block_navigation", "authored_hierarchy_navigation"],
        "navigation cells changed",
    )
    require(representation["searchArms"] == ["vector", "hybrid"], "retrieval cells changed")
    cells = len(representation["navigationArms"]) * len(representation["searchArms"])
    primary, robustness = questions["fresh"] * cells, repeat * cells
    total = primary + robustness
    cost = {}
    for model, prices in plan["costScenarios"]["referenceUncachedUsdPerMillionTokens"].items():
        cost[model] = {}
        for name, use in plan["costScenarios"]["perAnswerTurn"].items():
            tokens_in, tokens_out = use["cumulativeInputTokens"] * total, use["outputTokens"] * total
            cost[model][name] = {
                "providerInputTokens": tokens_in,
                "providerOutputTokens": tokens_out,
                "uncachedAnswerTokenPriceScenarioUsd": round(
                    (tokens_in * prices["input"] + tokens_out * prices["output"]) / 1e6, 2
                ),
            }
    review = plan["reviewPlanning"]
    extra = math.ceil(total * review["additionalIndependentAnswerFraction"])
    common = (
        questions["fresh"] * (review["goldAuthorMinutesPerQuestion"] + review["independentGoldMinutesPerQuestion"]) / 60
    )
    common += review["adjudicationAllowanceHours"] + review["sourceAuditAndReportingHours"]
    effort = {
        name: round(common + (total + extra) * review[key] / 60, 1)
        for name, key in [("low", "answerReviewMinutesPerTurnLow"), ("high", "answerReviewMinutesPerTurnHigh")]
    }
    return {
        "schema": "fli.source-confirmation.proposal-summary.v1",
        "status": "proposal_only",
        "documents": source["documents"],
        "uniqueFreshQuestions": questions["fresh"],
        "primaryCells": cells,
        "primaryAnswerTurns": primary,
        "prospectiveRobustnessTurns": robustness,
        "totalAnswerTurns": total,
        "primaryIndependentQuestionCount": questions["fresh"],
        "repeatsAreAdditionalIndependentQuestions": False,
        "referenceAnswerTokenPriceScenarios": cost,
        "additionalIndependentReviewedTurnsAtMinimum": extra,
        "manualEquivalentReviewHours": effort,
        "measuredTotalBill": None,
        "launchAuthorized": False,
        "cloudCalls": 0,
        "modelCalls": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=Path("docs/testing/source-confirmation-proposal/plan.json"))
    args = parser.parse_args()
    print(json.dumps(summarize(json.loads(args.plan.read_text())), indent=2))


if __name__ == "__main__":
    main()
