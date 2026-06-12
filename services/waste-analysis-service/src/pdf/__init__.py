def generate_waste_pdf(payload: dict) -> bytes:
    from src.pdf.builder import generate_waste_pdf as _generate_waste_pdf

    return _generate_waste_pdf(payload)


async def async_generate_waste_pdf(payload: dict) -> bytes:
    from src.pdf.builder import async_generate_waste_pdf as _async_generate_waste_pdf

    return await _async_generate_waste_pdf(payload)


__all__ = [
    "generate_waste_pdf",
    "async_generate_waste_pdf",
]
