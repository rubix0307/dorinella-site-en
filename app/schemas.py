"""Public API response models (v1)."""
from pydantic import BaseModel, Field

from .catalog import ProductType


class PlanOut(BaseModel):
    id: str
    title: str
    price_usd: int
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
    questions: list[TestQuestionOut]


class AttemptIn(BaseModel):
    answers: list[int] = Field(max_length=100)   # chosen option index per question, in question order
    consent: bool = False                        # the answers may describe health: they are stored only when the person agreed


class BandOut(BaseModel):
    key: str
    title: str
    text: str
    tips: list[str]
    doctor: str


class NoteOut(BaseModel):
    text: str
    warning: bool


class AttemptOut(BaseModel):
    score: int
    max_score: int
    band: BandOut
    notes: list[NoteOut]
    alert: bool                       # a warning sign was reported: the advice is to see a doctor first
    massage: bool                     # whether the result mentions abdominal self-massage and the paid material
    guide_url: str | None = None
    paid_guide_url: str | None = None
    course_url: str | None = None
