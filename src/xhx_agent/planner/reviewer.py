from __future__ import annotations

from xhx_agent.planner.modes import ReviewDecision
from xhx_agent.tools.terminal import TerminalResult


class Reviewer:
    def __init__(self) -> None:
        pass

    def review(
        self,
        task: str,
        changed_files: list[str],
        verification_results: list[TerminalResult]
    ) -> ReviewDecision:
        # If there are changed files but no verification results, block
        if changed_files and not verification_results:
            return ReviewDecision(
                passed=False,
                reason="Changed files were not verified by any commands.",
                needs_replan=True
            )

        # Check if any verification commands failed
        for result in verification_results:
            if result.status == "failed" or result.exit_code != 0:
                return ReviewDecision(
                    passed=False,
                    reason=f"Verification failed: {result.command} with exit code {result.exit_code}.",
                    needs_replan=True
                )

        return ReviewDecision(
            passed=True,
            reason="All changes verified successfully with zero errors."
        )
