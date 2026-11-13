"""The incident report the agent produces: root cause, evidence and one suggested fix."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "scaled-to-zero", "misconfiguration", "oom-killed", "image-pull-error", "bad-probe",
    "crash-loop", "resource-exhaustion", "network", "dependency-failure", "unknown",
    "no-incident",  # the investigation found nothing wrong (only known background noise, or recovered)
]


class SuggestedFix(BaseModel):
    action: Literal["scale_deployment", "rollout_undo", "rollout_restart", "none"] = Field(
        description="One of the allowlisted fix actions, or 'none' if no safe automatic fix exists.")
    deployment: str | None = Field(default=None, description="Deployment to act on, e.g. 'redis-cart'.")
    replicas: int | None = Field(default=None, description="Only for scale_deployment.")
    reason: str = Field(description="Why this fix addresses the root cause, in one sentence.")

    @property
    def command(self) -> str:
        if self.action == "scale_deployment":
            return f"kubectl scale deployment/{self.deployment} --replicas={self.replicas}"
        if self.action == "rollout_undo":
            return f"kubectl rollout undo deployment/{self.deployment}"
        if self.action == "rollout_restart":
            return f"kubectl rollout restart deployment/{self.deployment}"
        return "(no automatic fix; needs a human)"


class Report(BaseModel):
    summary: str = Field(description="One sentence a busy engineer can read: what broke and why, "
                                     "or 'No active incident: ...' when nothing is wrong.")
    root_cause: str = Field(description="The underlying cause, specific (names, values, ports, limits).")
    component: str = Field(description="The Kubernetes deployment that is the source of the problem, "
                                       "or 'none' when there is no active incident.")
    category: Category = Field(description="Type of failure.")
    evidence: list[str] = Field(description="3-6 concrete facts from tool results that prove the cause, "
                                            "each naming its source (event, log, rollout_history, metric).")
    user_impact: str = Field(description="What shoppers experience.")
    suggested_fix: SuggestedFix
    confidence: Literal["high", "medium", "low"]
    runbook: str | None = Field(default=None, description="Citation of the runbook section used, e.g. "
                                                          "'runbooks/oom-killed.md#fix', exactly as returned by "
                                                          "search_runbooks; null if none was used.")

    def to_text(self) -> str:
        lines = [
            f"Summary:     {self.summary}",
            f"Root cause:  {self.root_cause}",
            f"Component:   {self.component}  ({self.category})",
            f"User impact: {self.user_impact}",
            "Evidence:",
            *[f"  - {e}" for e in self.evidence],
            f"Suggested fix: {'none needed' if self.category == 'no-incident' else self.suggested_fix.command}",
            f"  why: {self.suggested_fix.reason}",
            f"Confidence:  {self.confidence}",
            f"Runbook:     {self.runbook or '(none)'}",
        ]
        return "\n".join(lines)
