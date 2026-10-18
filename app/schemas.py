"""Public API response models (v1)."""
from pydantic import BaseModel

from .catalog import ProductType


class PlanOut(BaseModel):
    id: str
    title: str
    price_uah: int
    features: list[str]
    gifts: list[str]
    featured: bool
    checkout_url: str


class ProductOut(BaseModel):
    slug: str
    type: ProductType
    title: str
    tagline: str
    url: str
    image_url: str


class ProductDetailOut(ProductOut):
    summary: str
    plans: list[PlanOut]


class TestQuestionOut(BaseModel):
    text: str
    options: list[str]


class SelfTestOut(BaseModel):
    slug: str
    title: str
    kind: str                     # 'score' (one total + bands) or 'scales' (several dimensions)
    questions: list[TestQuestionOut]


class AttemptIn(BaseModel):
    answers: list[int]   # chosen option index per question, in question order


class BandOut(BaseModel):
    key: str
    title: str
    text: str


class ScaleOut(BaseModel):
    key: str
    title: str
    description: str
    score: int
    min_score: int
    max_score: int
    reference: int
    above_reference: bool
    text: str


class AttemptOut(BaseModel):
    score: int | None = None
    max_score: int | None = None
    score_unit: str | None = None
    band: BandOut | None = None
    scales: list[ScaleOut] = []
    course_url: str | None = None
    product_url: str | None = None
