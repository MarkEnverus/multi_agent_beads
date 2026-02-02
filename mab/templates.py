"""MAB Team Templates - Predefined team configurations.

This module defines team templates that specify:
- Which agent roles are included
- How many of each role to spawn
- The workflow for bead handoffs between roles

Templates provide quick-start configurations for different use cases:
- solo: Single dev, human merges PRs
- pair: Dev + QA, human merges PRs
- full: Complete team with all roles
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class WorkflowStep(str, Enum):
    """Workflow steps that define how beads move through the team."""

    MANAGER = "manager"  # Manager creates and prioritizes beads
    TECH_LEAD = "tech_lead"  # Tech lead designs architecture
    DEV = "dev"  # Developer implements features/fixes
    QA = "qa"  # QA tests PRs
    REVIEWER = "reviewer"  # Code reviewer approves/merges
    HUMAN_MERGE = "human_merge"  # Human merges PRs (for smaller teams)
    DONE = "done"  # Bead completed


# Standard workflow patterns
WORKFLOW_DEV_ONLY = [WorkflowStep.DEV, WorkflowStep.HUMAN_MERGE]
WORKFLOW_DEV_QA = [WorkflowStep.DEV, WorkflowStep.QA, WorkflowStep.HUMAN_MERGE]
WORKFLOW_FULL = [
    WorkflowStep.MANAGER,
    WorkflowStep.TECH_LEAD,
    WorkflowStep.DEV,
    WorkflowStep.QA,
    WorkflowStep.REVIEWER,
    WorkflowStep.DONE,
]


@dataclass
class TeamTemplate:
    """Defines a team configuration template.

    Attributes:
        name: Template identifier (solo, pair, full).
        description: Human-readable description.
        roles: Dict mapping role names to worker counts.
        workflow: List of WorkflowSteps defining the bead lifecycle.
    """

    name: str
    description: str
    roles: dict[str, int] = field(default_factory=dict)
    workflow: list[WorkflowStep] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "description": self.description,
            "roles": self.roles,
            "workflow": [step.value for step in self.workflow],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TeamTemplate":
        """Create TeamTemplate from dictionary."""
        workflow = [WorkflowStep(step) for step in data.get("workflow", [])]
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            roles=data.get("roles", {}),
            workflow=workflow,
        )

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str) -> "TeamTemplate":
        """Deserialize from JSON string."""
        return cls.from_dict(json.loads(json_str))

    def get_total_workers(self) -> int:
        """Get total number of workers defined by this template."""
        return sum(self.roles.values())

    def get_role_names(self) -> list[str]:
        """Get list of role names in this template."""
        return list(self.roles.keys())

    def has_role(self, role: str) -> bool:
        """Check if template includes a specific role."""
        return role in self.roles and self.roles[role] > 0


# Predefined templates
TEMPLATES: dict[str, TeamTemplate] = {
    "solo": TeamTemplate(
        name="solo",
        description="Single developer, human merges PRs",
        roles={"dev": 1},
        workflow=WORKFLOW_DEV_ONLY,
    ),
    "pair": TeamTemplate(
        name="pair",
        description="Developer + QA, human merges PRs",
        roles={"dev": 1, "qa": 1},
        workflow=WORKFLOW_DEV_QA,
    ),
    "full": TeamTemplate(
        name="full",
        description="Complete team with all roles",
        roles={"manager": 1, "tech_lead": 1, "dev": 1, "qa": 1, "reviewer": 1},
        workflow=WORKFLOW_FULL,
    ),
}


def get_template(name: str) -> TeamTemplate | None:
    """Get a template by name.

    Args:
        name: Template name (solo, pair, full).

    Returns:
        TeamTemplate if found, None otherwise.
    """
    return TEMPLATES.get(name)


def get_template_names() -> list[str]:
    """Get list of available template names."""
    return list(TEMPLATES.keys())


def validate_template_name(name: str) -> bool:
    """Check if a template name is valid.

    Args:
        name: Template name to validate.

    Returns:
        True if valid template name, False otherwise.
    """
    return name in TEMPLATES


def get_workflow_for_role(template: TeamTemplate, current_role: str) -> str | None:
    """Get the next workflow step for a given role.

    Args:
        template: The team template.
        current_role: Current role completing work.

    Returns:
        Next role in the workflow, or None if no next step.
    """
    workflow = template.workflow

    # Find current role in workflow
    try:
        current_step = WorkflowStep(current_role)
        current_index = workflow.index(current_step)
    except (ValueError, KeyError):
        return None

    # Get next step
    if current_index + 1 < len(workflow):
        next_step = workflow[current_index + 1]
        # Skip human_merge for automated teams
        if next_step == WorkflowStep.HUMAN_MERGE:
            return "human_merge"
        if next_step == WorkflowStep.DONE:
            return "done"
        # WorkflowStep inherits from str, so value is always str
        return str(next_step.value)

    return None


class TemplateError(Exception):
    """Base exception for template operations."""

    pass


class TemplateNotFoundError(TemplateError):
    """Raised when template is not found."""

    pass
