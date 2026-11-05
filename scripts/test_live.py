"""Live semantic tests against the real System One upstream.

These calls cost money on every run, so they only execute when explicitly
enabled:

    LIVE=1 SYSTEM_ONE_MODEL=typesafe/jev-1.13 \\
        MCP_URL=http://127.0.0.1:8080/mcp python scripts/test_live.py

What is asserted here is *semantics*, not just "did it 200": that Choice returns
one of the offered options, that Score stays inside the level range and keeps
fractional precision, that Noul returns a probability rather than a boolean, and
that batching maps ids both ways.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import Runner, exit_on_error, tool_error_payload, tool_payload  # noqa: E402
from mcp_client import McpClient  # noqa: E402

URL = os.environ.get("MCP_URL", "http://127.0.0.1:8080/mcp")
MODEL = os.environ.get("SYSTEM_ONE_MODEL")

STATE = (
    "订单 #A-1042 的客户来信：本月同一笔 399 元订单被扣款两次，"
    "已经等了两天，希望今天内给出处理方案，否则会投诉到平台。"
)

LEVELS = ["常规审核", "及时回复", "立即由人工处理"]


def main() -> None:
    if os.environ.get("LIVE") != "1":
        print(
            "live tests are disabled because they bill the upstream account.\n"
            "re-run with LIVE=1 to spend a few thousandths of a cent per call."
        )
        return

    runner = Runner("live")

    with McpClient(URL) as client:
        client.initialize()

        runner.section("Choice: one batch, two questions")
        choice_result = client.call_tool(
            "Choice",
            {
                "state": STATE,
                "questions": [
                    {
                        "id": "team",
                        "instructions": "选择负责审核的团队。",
                        "criteria": [
                            {"name": "billing", "description": "付款、退款与扣款争议"},
                            {"name": "support", "description": "产品使用与技术支持"},
                        ],
                    },
                    {
                        "id": "channel",
                        "instructions": "选择首次回复的渠道。",
                        "criteria": [
                            {"name": "email", "description": "邮件回复"},
                            {"name": "phone", "description": "电话联系"},
                        ],
                    },
                ],
            },
        )
        runner.check("Choice: not an error", not choice_result.get("isError"), choice_result)
        choice = tool_payload(choice_result)
        runner.case("Choice: answers keyed by question id",
                    lambda: _check_keys(runner, choice, "answers", {"channel", "team"}))
        _check_envelope(runner, choice)

        team = choice["answers"]["team"]
        runner.equal("Choice: type", team.get("type"), "choice")
        runner.check(
            "Choice: chosen option is one of the criteria",
            team.get("choice") in {"billing", "support"},
            team.get("choice"),
        )
        _check_probabilities(runner, team.get("probabilities"), {"billing", "support"}, "Choice")
        _check_confidence(runner, team, "Choice")
        runner.check(
            "Choice: probabilities align with the chosen option",
            _argmax(team.get("probabilities") or {}) == team.get("choice"),
            {"choice": team.get("choice"), "probs": team.get("probabilities")},
        )

        runner.section("Score: ordered levels, precision preserved")
        score_result = client.call_tool(
            "Score",
            {
                "state": STATE,
                "questions": [
                    {
                        "id": "urgency",
                        "instructions": "评估这件事的紧急程度。",
                        "criteria": LEVELS,
                    }
                ],
            },
        )
        runner.check("Score: not an error", not score_result.get("isError"), score_result)
        score = tool_payload(score_result)
        _check_envelope(runner, score)

        urgency = score["answers"]["urgency"]
        runner.equal("Score: type", urgency.get("type"), "score")
        value = urgency.get("score")
        runner.check("Score: score is a number", isinstance(value, (int, float)), value)
        runner.check(
            "Score: score inside level range",
            isinstance(value, (int, float)) and 0 <= value <= len(LEVELS) - 1,
            value,
        )
        # Whether the model lands on a fractional score varies per run, so this
        # is a skip rather than a failure when the sample is integral. The
        # no-rounding guarantee itself is covered by Rust unit tests.
        if isinstance(value, float) and value % 1 != 0:
            runner.check("Score: fractional precision preserved", True)
        else:
            runner.skip(
                "Score: fractional precision preserved",
                f"upstream returned an integral score ({value})",
            )
        runner.equal("Score: legend maps every level", urgency.get("legend"),
                     {str(i): name for i, name in enumerate(LEVELS)})
        _check_probabilities(
            runner, urgency.get("probabilities"), {str(i) for i in range(len(LEVELS))}, "Score"
        )
        _check_confidence(runner, urgency, "Score")

        runner.section("Noul: probability, not boolean")
        noul_result = client.call_tool(
            "Noul",
            {
                "state": STATE,
                "questions": [
                    {"id": "duplicate", "instructions": "信件是否描述了重复扣款？"},
                    {
                        "id": "angry",
                        "instructions": "客户语气是否带有明显不满？",
                        "criteria": {"true": "明确表达不满或威胁投诉", "false": "语气平静"},
                    },
                ],
            },
        )
        runner.check("Noul: not an error", not noul_result.get("isError"), noul_result)
        noul = tool_payload(noul_result)
        runner.case("Noul: answers keyed by question id",
                    lambda: _check_keys(runner, noul, "answers", {"duplicate", "angry"}))
        _check_envelope(runner, noul)

        duplicate = noul["answers"]["duplicate"]
        runner.equal("Noul: type", duplicate.get("type"), "noul")
        probability = duplicate.get("noul")
        runner.check(
            "Noul: value is a float in 0..1",
            isinstance(probability, float) and 0.0 <= probability <= 1.0,
            probability,
        )
        runner.check(
            "Noul: not coerced to a boolean",
            not isinstance(probability, bool),
            type(probability).__name__,
        )
        runner.check(
            "Noul: no boolean field was substituted",
            "boolean" not in duplicate and "answer" not in duplicate,
            sorted(duplicate),
        )

        runner.section("idempotency_key is echoed")
        fixed = "live-test-fixed-key"
        retry_result = client.call_tool(
            "Noul",
            {
                "state": STATE,
                "questions": [{"id": "dup", "instructions": "是否存在重复扣款？"}],
                "idempotency_key": fixed,
            },
        )
        runner.check("idempotency run: not an error", not retry_result.get("isError"))
        runner.equal(
            "idempotency_key round-tripped",
            tool_payload(retry_result).get("idempotency_key"),
            fixed,
        )
        replay = tool_payload(retry_result).get("meta", {}).get("replayed")
        if replay is None:
            runner.skip(
                "upstream replayed header",
                "no X-Idempotency-Replayed header on this endpoint",
            )
        else:
            runner.check("upstream reports replay", True)

        # Most expensive call in the suite, so it runs inside the session after
        # the cheap checks: proves the documented 32-question ceiling is really
        # accepted and that every id maps back.
        runner.section("batch ceiling: 32 questions in one call")
        ids = {f"q{i}" for i in range(32)}
        ceiling_result = client.call_tool(
            "Noul",
            {
                "state": " ".join(f"第 {i} 项：待确认事项。" for i in range(32)),
                "questions": [
                    {"id": qid, "instructions": f"第 {qid[1:]} 项是否需要人工确认？"}
                    for qid in sorted(ids)
                ],
            },
        )
        runner.check(
            "ceiling: not an error", not ceiling_result.get("isError"), ceiling_result
        )
        ceiling = tool_payload(ceiling_result)
        actual_ids = set((ceiling.get("answers") or {}).keys())
        runner.equal("ceiling: every id answered", actual_ids, ids)
        runner.check(
            "ceiling: all values are probabilities",
            all(
                isinstance(answer.get("noul"), float)
                and 0.0 <= answer["noul"] <= 1.0
                for answer in (ceiling.get("answers") or {}).values()
            ),
            ceiling.get("answers"),
        )

    exit_on_error(runner)


def _check_keys(runner: Runner, payload: dict, key: str, expected: set[str]) -> None:
    actual = set((payload.get(key) or {}).keys())
    runner.equal(f"answers match the requested ids", actual, expected)


def _check_envelope(runner: Runner, payload: dict) -> None:
    """Fields the server adds on top of the upstream body."""
    runner.check("envelope: requested_model present", bool(payload.get("requested_model")),
                 payload.get("requested_model"))
    if MODEL:
        runner.equal("envelope: requested_model matches env", payload.get("requested_model"), MODEL)
    runner.check("envelope: served model reported", bool(payload.get("model")),
                 payload.get("model"))
    runner.check("envelope: idempotency_key present", bool(payload.get("idempotency_key")))

    usage = payload.get("usage") or {}
    cost = usage.get("cost")
    runner.check("envelope: usage.cost reported", isinstance(cost, (int, float)), usage)
    runner.check("envelope: usage.cost is positive",
                 isinstance(cost, (int, float)) and cost > 0, cost)


def _check_probabilities(
    runner: Runner, probabilities: dict | None, expected_keys: set[str], label: str
) -> None:
    runner.check(f"{label}: probabilities present", isinstance(probabilities, dict), probabilities)
    if not isinstance(probabilities, dict):
        return
    runner.equal(f"{label}: probability keys", set(probabilities), expected_keys)
    total = sum(probabilities.values())
    runner.check(
        f"{label}: probabilities sum to ~1",
        0.97 <= total <= 1.03,
        f"sum={total}, probs={probabilities}",
    )


def _check_confidence(runner: Runner, answer: dict, label: str) -> None:
    confidence = answer.get("confidence")
    runner.check(
        f"{label}: confidence is a probability",
        isinstance(confidence, (int, float)) and 0.0 <= confidence <= 1.0,
        confidence,
    )


def _argmax(probabilities: dict) -> str | None:
    if not probabilities:
        return None
    return max(probabilities.items(), key=lambda item: item[1])[0]


if __name__ == "__main__":
    main()
