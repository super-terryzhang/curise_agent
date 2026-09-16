"""Product prices: finite, non-negative NUMERIC(10, 2), rounded half up."""
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any


def parse_product_price(value: Any) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError("价格必须是非负数字")
    text = str(value).strip().replace("，", ",")
    text = re.sub(r"^[¥￥$€£]\s*", "", text)
    if "," in text:
        if not re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", text):
            raise ValueError("价格千分位格式无效")
        text = text.replace(",", "")
    try:
        amount = Decimal(text)
        if not amount.is_finite() or amount < 0 or amount > Decimal("99999999.99"):
            raise ValueError("价格必须在 0 至 99999999.99 之间")
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("价格必须是数字，不能是公式或其他文本") from exc
