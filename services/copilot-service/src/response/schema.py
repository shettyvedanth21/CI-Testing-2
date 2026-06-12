from typing import Any, List, Optional

from pydantic import BaseModel, Field


class CuratedContext(BaseModel):
    device_id: Optional[str] = None


class ChartDataset(BaseModel):
    label: str
    data: List[float | int]


class Chart(BaseModel):
    type: str
    title: str
    labels: List[str]
    datasets: List[ChartDataset]


class DataTable(BaseModel):
    headers: List[str]
    rows: List[List[Any]]


class PageLink(BaseModel):
    label: str
    route: str


class ReasoningSections(BaseModel):
    what_happened: str
    why_it_matters: str
    how_calculated: str


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_history: List[ChatTurn] = Field(default_factory=list)
    curated_context: Optional[CuratedContext] = None


class CuratedQuestionItem(BaseModel):
    id: str
    text: str


class CuratedQuestionsResponse(BaseModel):
    starter_questions: List[CuratedQuestionItem] = Field(default_factory=list)


class CopilotResponse(BaseModel):
    answer: str
    reasoning: str
    reasoning_sections: Optional[ReasoningSections] = None
    data_table: Optional[DataTable] = None
    chart: Optional[Chart] = None
    page_links: Optional[List[PageLink]] = None
    follow_up_suggestions: List[str] = Field(default_factory=list)
    curated_context: Optional[CuratedContext] = None
    error_code: Optional[str] = None
