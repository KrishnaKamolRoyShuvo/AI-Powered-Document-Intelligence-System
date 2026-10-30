from pydantic import BaseModel, Field


class LineItem(BaseModel):
    description: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = None


class InvoiceData(BaseModel):
    invoice_number: str | None = None
    invoice_date: str | None = Field(default=None, description="YYYY-MM-DD")
    supplier_name: str | None = None
    customer_name: str | None = None
    subtotal: float | None = None
    tax: float | None = None
    discount: float | None = None
    total: float | None = None
    currency: str | None = None
    line_items: list[LineItem] = []
    uncertain_fields: list[str] = []
