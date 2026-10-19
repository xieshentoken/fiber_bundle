"""Authenticated system settings and read-only usage reporting."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from . import auth, database, model_budgets, system_fonts, system_settings, usage_report
from .config import Config
from .execution_config import ExecutionConfig
from .http_guard import Settings


class LoginPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_ttl: StrictInt = Field(ge=1, le=86400)
    expected_revision: StrictInt = Field(ge=0)


def router(
    config: Config, settings: Settings, execution: ExecutionConfig | None = None
) -> APIRouter:
    routes = APIRouter(prefix="/admin/system")

    @routes.get("/model-budgets")
    def budgets(request: Request) -> dict[str, Any]:
        with database.connect(config.state_root) as db:
            auth.admin(db, request.cookies.get(auth.COOKIE, ""))
            return {
                "budgets": model_budgets.inspect(db),
                "credentials": usage_report.configured(config, execution, db),
            }

    @routes.put("/model-budgets")
    def save_budget(data: model_budgets.Save, request: Request) -> dict[str, Any]:
        with database.connect(config.state_root) as db:
            auth.admin(
                db, request.cookies.get(auth.COOKIE, ""), request.headers.get("x-csrf-token", "")
            )
            with database.transaction(db):
                if data.secret_file is None:
                    from . import query_policy_store
                    from .config import InvalidInput
                    from .usage_credentials import reference

                    route = query_policy_store.current(config)
                    configured = [
                        p for p in (execution, route.cloud_answer if route else None) if p
                    ]
                    match = next(
                        (
                            p
                            for p in configured
                            if reference(p.secret_file, db)["id"] == data.credential_id
                        ),
                        None,
                    )
                    alias = db.execute(
                        "SELECT secret_file FROM model_credential_aliases "
                        "WHERE credential_id=? LIMIT 1",
                        (data.credential_id,),
                    ).fetchone()
                    secret_file = match.secret_file if match else alias[0] if alias else None
                    if secret_file is None:
                        raise InvalidInput("budget_credential_unconfigured")
                    data = data.model_copy(update={"secret_file": secret_file})
                return model_budgets.save(db, data)

    @routes.get("/fonts")
    def get_fonts(request: Request) -> dict[str, Any]:
        db = database.connect(config.state_root)
        try:
            auth.admin(db, request.cookies.get(auth.COOKIE, ""))
        finally:
            db.close()
        try:
            return system_fonts.installed_fonts()
        except system_fonts.FontCatalogError:
            raise HTTPException(503, "installed fonts are temporarily unavailable") from None

    @routes.get("/settings")
    def get_settings(request: Request) -> dict[str, Any]:
        db = database.connect(config.state_root)
        try:
            auth.admin(db, request.cookies.get(auth.COOKIE, ""))
            return system_settings.inspect(db, settings.session_ttl)
        finally:
            db.close()

    @routes.put("/settings")
    def put_settings(data: LoginPolicy, request: Request) -> dict[str, Any]:
        db = database.connect(config.state_root)
        try:
            auth.admin(
                db, request.cookies.get(auth.COOKIE, ""), request.headers.get("x-csrf-token", "")
            )
            return system_settings.save(
                db, settings.session_ttl, data.session_ttl, data.expected_revision
            )
        except system_settings.Conflict:
            raise HTTPException(409, "settings changed; refresh before saving") from None
        finally:
            db.close()

    @routes.get("/usage")
    def get_usage(
        request: Request,
        start: str | None = Query(default=None, max_length=10),
        end: str | None = Query(default=None, max_length=10),
        api_key_id: str = Query(default="all", max_length=68),
    ) -> dict[str, Any]:
        db = database.connect(config.state_root)
        try:
            auth.admin(db, request.cookies.get(auth.COOKIE, ""))
            # One read transaction keeps all chart totals consistent while workers settle calls.
            db.execute("BEGIN")
            return usage_report.report(
                config, db, start=start, end=end, api_key_id=api_key_id, execution=execution
            )
        finally:
            db.close()

    return routes
