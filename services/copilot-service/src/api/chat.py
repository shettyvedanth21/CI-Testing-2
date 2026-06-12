from fastapi import APIRouter, Request

from src.ai.copilot_engine import CopilotEngine
from src.ai.model_client import AIUnavailableError, ModelClient
from src.config import settings
from src.integrations.service_clients import get_current_tariff
from src.rate_limit import limiter
from src.response.schema import ChatRequest, CopilotResponse, CuratedQuestionItem, CuratedQuestionsResponse
from src.templates.curated_catalog import get_starter_questions
from services.shared.tenant_context import TenantContext


router = APIRouter()
model_client: ModelClient | None = None
engine: CopilotEngine | None = None


def _get_engine() -> tuple[ModelClient | None, CopilotEngine | None]:
    global model_client, engine
    if engine is not None:
        return model_client, engine
    try:
        model_client = ModelClient()
    except Exception:
        model_client = None
    try:
        engine = CopilotEngine(model_client=model_client)
        return model_client, engine
    except Exception:
        return None, None


@router.post("/api/v1/copilot/chat", response_model=CopilotResponse)
@limiter.limit(settings.copilot_chat_rate_limit)
async def chat(request: Request, body: ChatRequest) -> CopilotResponse:
    _, copilot_engine = _get_engine()
    if not copilot_engine:
        return CopilotResponse(
            answer="Something went wrong. Please try again.",
            reasoning="Copilot engine initialization failed.",
            error_code="NOT_CONFIGURED",
        )

    tenant_id = TenantContext.from_request(request).require_tenant()
    tariff_rate, currency = await get_current_tariff(tenant_id)

    history_payload = [t.model_dump() for t in body.conversation_history]

    try:
        return await copilot_engine.process_question(
            message=body.message,
            history=history_payload,
            tariff_rate=tariff_rate,
            currency=currency,
            tenant_id=tenant_id,
            curated_context=body.curated_context,
        )
    except AIUnavailableError:
        return CopilotResponse(
            answer="AI service is temporarily unavailable. Please try again.",
            reasoning="Provider request failed.",
            error_code="AI_UNAVAILABLE",
        )
    except Exception:
        return CopilotResponse(
            answer="Something went wrong. Please try again.",
            reasoning="Unexpected server error while processing copilot request.",
            error_code="INTERNAL_ERROR",
        )


@router.get("/api/v1/copilot/curated-questions", response_model=CuratedQuestionsResponse)
async def curated_questions(_: Request) -> CuratedQuestionsResponse:
    return CuratedQuestionsResponse(
        starter_questions=[CuratedQuestionItem(id=question.id, text=question.text) for question in get_starter_questions()]
    )
