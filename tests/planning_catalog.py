"""Golden fixture catalog for Milestone 4 ImplementationPlan contract testing.

Defines expected ImplementationPlan structures for representative issues to
stabilise and test the planning contract before PlannerAgent is implemented.
"""

from __future__ import annotations

from dataclasses import dataclass

from openforge.models import ImplementationPlan


@dataclass(frozen=True)
class PlanningFixture:
    id: str
    description: str
    issue_title: str
    expected_plan: ImplementationPlan


PLANNING_CATALOG: list[PlanningFixture] = [
    PlanningFixture(
        id="plan-fastapi-422-fix",
        description="Plan for FastAPI 422 Unprocessable Entity fix",
        issue_title="422 Unprocessable Entity when posting nested JSON body",
        expected_plan=ImplementationPlan(
            goal="Support nested JSON address validation in create_user endpoint.",
            files_to_modify=["routers/users.py", "schemas/users.py"],
            implementation_steps=[
                "Define AddressSchema Pydantic model in schemas/users.py",
                "Update UserCreate schema in schemas/users.py to use AddressSchema for address field",
                "Update create_user endpoint in routers/users.py to accept UserCreate model",
                "Add test case for nested address JSON payload in tests/test_users.py",
            ],
            tests_to_run=["pytest tests/test_users.py"],
            risks=["Clients sending flat address strings will receive 422 validation error"],
            requires_human=False,
        ),
    ),
    PlanningFixture(
        id="plan-django-role-permissions",
        description="Plan for Django role-based permissions feature",
        issue_title="Add role-based permissions to user model",
        expected_plan=ImplementationPlan(
            goal="Implement Role model and link to User with permission checks.",
            files_to_modify=[
                "accounts/models.py",
                "views/dashboard.py",
                "tests/test_permissions.py",
            ],
            implementation_steps=[
                "Add Role model and ForeignKey in accounts/models.py",
                "Generate and run Django database migration under accounts/migrations/",
                "Add permission check decorator in views/dashboard.py",
                "Add role-based permission test cases in tests/test_permissions.py",
            ],
            tests_to_run=["python manage.py test accounts"],
            risks=["Existing user accounts will have null role until assigned"],
            requires_human=False,
        ),
    ),
    PlanningFixture(
        id="plan-vague-crash-human-gate",
        description="Plan for vague crash report requiring human sign-off",
        issue_title="App crashes after update",
        expected_plan=ImplementationPlan(
            goal="Request reproduction steps and environment logs from reporter.",
            files_to_modify=[],
            implementation_steps=[
                "Ask reporter for operating system details, crash log, and reproduction steps",
            ],
            tests_to_run=[],
            risks=["Cannot proceed with code modification without error details"],
            requires_human=True,
        ),
    ),
]
