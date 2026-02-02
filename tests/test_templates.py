"""Tests for mab.templates module."""

import json

import pytest

from mab.templates import (
    TEMPLATES,
    TeamTemplate,
    TemplateError,
    TemplateNotFoundError,
    WorkflowStep,
    get_template,
    get_template_names,
    get_workflow_for_role,
    validate_template_name,
)


class TestWorkflowStep:
    """Tests for WorkflowStep enum."""

    def test_workflow_step_values(self):
        """Test that workflow steps have expected string values."""
        assert WorkflowStep.MANAGER.value == "manager"
        assert WorkflowStep.TECH_LEAD.value == "tech_lead"
        assert WorkflowStep.DEV.value == "dev"
        assert WorkflowStep.QA.value == "qa"
        assert WorkflowStep.REVIEWER.value == "reviewer"
        assert WorkflowStep.HUMAN_MERGE.value == "human_merge"
        assert WorkflowStep.DONE.value == "done"


class TestTeamTemplate:
    """Tests for TeamTemplate dataclass."""

    def test_to_dict(self):
        """Test conversion to dictionary."""
        template = TeamTemplate(
            name="test",
            description="Test template",
            roles={"dev": 2, "qa": 1},
            workflow=[WorkflowStep.DEV, WorkflowStep.QA],
        )

        result = template.to_dict()

        assert result["name"] == "test"
        assert result["description"] == "Test template"
        assert result["roles"] == {"dev": 2, "qa": 1}
        assert result["workflow"] == ["dev", "qa"]

    def test_from_dict(self):
        """Test creation from dictionary."""
        data = {
            "name": "custom",
            "description": "Custom template",
            "roles": {"dev": 1},
            "workflow": ["dev", "human_merge"],
        }

        template = TeamTemplate.from_dict(data)

        assert template.name == "custom"
        assert template.description == "Custom template"
        assert template.roles == {"dev": 1}
        assert template.workflow == [WorkflowStep.DEV, WorkflowStep.HUMAN_MERGE]

    def test_to_json(self):
        """Test JSON serialization."""
        template = TeamTemplate(
            name="test",
            description="Test",
            roles={"dev": 1},
            workflow=[WorkflowStep.DEV],
        )

        json_str = template.to_json()
        data = json.loads(json_str)

        assert data["name"] == "test"
        assert data["roles"] == {"dev": 1}

    def test_from_json(self):
        """Test JSON deserialization."""
        json_str = (
            '{"name": "test", "description": "Test", "roles": {"dev": 1}, "workflow": ["dev"]}'
        )

        template = TeamTemplate.from_json(json_str)

        assert template.name == "test"
        assert template.roles == {"dev": 1}

    def test_get_total_workers(self):
        """Test total worker count calculation."""
        template = TeamTemplate(
            name="test",
            description="Test",
            roles={"dev": 2, "qa": 1, "reviewer": 1},
            workflow=[],
        )

        assert template.get_total_workers() == 4

    def test_get_role_names(self):
        """Test getting list of role names."""
        template = TeamTemplate(
            name="test",
            description="Test",
            roles={"dev": 1, "qa": 1},
            workflow=[],
        )

        assert set(template.get_role_names()) == {"dev", "qa"}

    def test_has_role(self):
        """Test checking if template has a role."""
        template = TeamTemplate(
            name="test",
            description="Test",
            roles={"dev": 1, "qa": 0},
            workflow=[],
        )

        assert template.has_role("dev") is True
        assert template.has_role("qa") is False  # count is 0
        assert template.has_role("reviewer") is False


class TestPredefinedTemplates:
    """Tests for predefined TEMPLATES."""

    def test_solo_template(self):
        """Test solo template configuration."""
        solo = TEMPLATES["solo"]

        assert solo.name == "solo"
        assert solo.roles == {"dev": 1}
        assert solo.get_total_workers() == 1
        assert WorkflowStep.DEV in solo.workflow
        assert WorkflowStep.HUMAN_MERGE in solo.workflow

    def test_pair_template(self):
        """Test pair template configuration."""
        pair = TEMPLATES["pair"]

        assert pair.name == "pair"
        assert pair.roles == {"dev": 1, "qa": 1}
        assert pair.get_total_workers() == 2
        assert WorkflowStep.DEV in pair.workflow
        assert WorkflowStep.QA in pair.workflow
        assert WorkflowStep.HUMAN_MERGE in pair.workflow

    def test_full_template(self):
        """Test full template configuration."""
        full = TEMPLATES["full"]

        assert full.name == "full"
        assert full.roles == {"manager": 1, "tech_lead": 1, "dev": 1, "qa": 1, "reviewer": 1}
        assert full.get_total_workers() == 5
        assert WorkflowStep.MANAGER in full.workflow
        assert WorkflowStep.TECH_LEAD in full.workflow
        assert WorkflowStep.DEV in full.workflow
        assert WorkflowStep.QA in full.workflow
        assert WorkflowStep.REVIEWER in full.workflow
        assert WorkflowStep.DONE in full.workflow
        # Full team doesn't need HUMAN_MERGE
        assert WorkflowStep.HUMAN_MERGE not in full.workflow


class TestTemplateFunctions:
    """Tests for template helper functions."""

    def test_get_template_valid(self):
        """Test getting a valid template."""
        template = get_template("solo")
        assert template is not None
        assert template.name == "solo"

    def test_get_template_invalid(self):
        """Test getting an invalid template returns None."""
        template = get_template("nonexistent")
        assert template is None

    def test_get_template_names(self):
        """Test getting list of template names."""
        names = get_template_names()
        assert "solo" in names
        assert "pair" in names
        assert "full" in names

    def test_validate_template_name_valid(self):
        """Test validating valid template names."""
        assert validate_template_name("solo") is True
        assert validate_template_name("pair") is True
        assert validate_template_name("full") is True

    def test_validate_template_name_invalid(self):
        """Test validating invalid template names."""
        assert validate_template_name("invalid") is False
        assert validate_template_name("") is False


class TestWorkflowNavigation:
    """Tests for workflow navigation functions."""

    def test_get_workflow_for_role_dev_in_pair(self):
        """Test getting next step for dev in pair template."""
        pair = TEMPLATES["pair"]
        next_role = get_workflow_for_role(pair, "dev")
        assert next_role == "qa"

    def test_get_workflow_for_role_qa_in_pair(self):
        """Test getting next step for qa in pair template."""
        pair = TEMPLATES["pair"]
        next_role = get_workflow_for_role(pair, "qa")
        assert next_role == "human_merge"

    def test_get_workflow_for_role_dev_in_full(self):
        """Test getting next step for dev in full template."""
        full = TEMPLATES["full"]
        next_role = get_workflow_for_role(full, "dev")
        assert next_role == "qa"

    def test_get_workflow_for_role_reviewer_in_full(self):
        """Test getting next step for reviewer in full template."""
        full = TEMPLATES["full"]
        next_role = get_workflow_for_role(full, "reviewer")
        assert next_role == "done"

    def test_get_workflow_for_role_invalid(self):
        """Test getting next step for invalid role."""
        pair = TEMPLATES["pair"]
        next_role = get_workflow_for_role(pair, "invalid_role")
        assert next_role is None


class TestTemplateExceptions:
    """Tests for template exceptions."""

    def test_template_error(self):
        """Test TemplateError base exception."""
        with pytest.raises(TemplateError):
            raise TemplateError("Test error")

    def test_template_not_found_error(self):
        """Test TemplateNotFoundError exception."""
        with pytest.raises(TemplateNotFoundError):
            raise TemplateNotFoundError("Template not found")

        # Should also be catchable as TemplateError
        with pytest.raises(TemplateError):
            raise TemplateNotFoundError("Template not found")
