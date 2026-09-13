"""Shared building blocks: targeting strategies, detectors, wait specs.

Reused across steps, outcomes, recoveries, and success conditions so that
"how do we know this is true" is expressed the same way everywhere.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

# --- Targeting strategies, ordered by stability (most stable first) --------


class A11yStrategy(BaseModel):
    kind: Literal["a11y"] = "a11y"
    role: str
    name: str


class LabelStrategy(BaseModel):
    kind: Literal["label"] = "label"
    label_text: str
    control: str


class SpatialStrategy(BaseModel):
    kind: Literal["spatial"] = "spatial"
    anchor_text: str
    direction: Literal["up", "down", "left", "right"]


class DomStrategy(BaseModel):
    kind: Literal["dom"] = "dom"
    css: str | None = None
    xpath: str | None = None

    def model_post_init(self, __context: object) -> None:
        if self.css is None and self.xpath is None:
            raise ValueError("dom strategy requires css or xpath")


TargetStrategy = Annotated[
    Union[A11yStrategy, LabelStrategy, SpatialStrategy, DomStrategy],
    Field(discriminator="kind"),
]


class Target(BaseModel):
    description: str
    strategies: list[TargetStrategy] = Field(min_length=1)


class TargetRef(BaseModel):
    """A pointer to a target declared elsewhere in the artifact.

    e.g. {"$ref": "self.target"} or {"$ref": "steps.step_005.target"}.
    Resolution is the engine's job; the schema only carries the pointer.
    """

    model_config = ConfigDict(populate_by_name=True)

    ref: str = Field(alias="$ref")


TargetOrRef = Union[Target, TargetRef]


# --- Wait specs --------------------------------------------------------------


class WaitSpec(BaseModel):
    strategy: str
    timeout_ms: int = Field(gt=0)


# --- Detectors ---------------------------------------------------------------
#
# The vocabulary a step checkpoint, a declared outcome, or a declared recovery
# uses to say "this condition holds". Composable via any_of/all_of so a single
# declared condition can cover more than one on-screen signal.


class ElementPresentDetector(BaseModel):
    kind: Literal["element_present"] = "element_present"
    target: TargetOrRef


class ElementAbsentDetector(BaseModel):
    kind: Literal["element_absent"] = "element_absent"
    target: TargetOrRef


class TextMatchesDetector(BaseModel):
    kind: Literal["text_matches"] = "text_matches"
    pattern: str


class ValueEqualsDetector(BaseModel):
    kind: Literal["value_equals"] = "value_equals"
    target: TargetOrRef
    expected: str


class UrlMatchesDetector(BaseModel):
    kind: Literal["url_matches"] = "url_matches"
    pattern: str


SimpleDetector = Annotated[
    Union[
        ElementPresentDetector,
        ElementAbsentDetector,
        TextMatchesDetector,
        ValueEqualsDetector,
        UrlMatchesDetector,
    ],
    Field(discriminator="kind"),
]


class AnyOfDetector(BaseModel):
    any_of: list["Detector"] = Field(min_length=1)


class AllOfDetector(BaseModel):
    all_of: list["Detector"] = Field(min_length=1)


Detector = Union[SimpleDetector, AnyOfDetector, AllOfDetector]

AnyOfDetector.model_rebuild()
AllOfDetector.model_rebuild()
