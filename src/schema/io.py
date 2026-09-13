"""Typed parameters: what the caller supplies and what it gets back."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from src.schema.common import TargetOrRef

ParamType = Literal["string", "integer", "decimal", "money", "date", "boolean", "enum"]
Transform = Literal["parse_currency", "trim", "parse_date", "raw"]


class InputParam(BaseModel):
    name: str
    type: ParamType
    required: bool = True
    sensitive: bool = False
    description: str
    example: Any | None = None
    constraints: dict[str, Any] | None = None


class OutputSource(BaseModel):
    step_id: str
    target: TargetOrRef | None = None
    transform: Transform = "raw"


class OutputParam(BaseModel):
    name: str
    type: ParamType
    required: bool = True
    sensitive: bool = False
    description: str
    source: OutputSource
