"""OrderCostItem ORM — user-entered extra expenses per order.

One row per cost line: freight, customs, insurance, labor, etc. Stored
in the row's native currency; converted to the order's display currency
on read by `compute_pnl`. Cascade-deletes with the parent order so
removing an order leaves no orphan rows.

Schema lives in `migrations/versions/0012_order_cost_items.py`.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.db.base import Base


class OrderCostItem(Base):
    __tablename__ = "v3_order_cost_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("v2_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Free-form category. The UI offers a preset list (运费 / 港口费用 /
    # 报关费用 / 保险费用 / 仓储费用 / 本地配送费用 / 人工费 / 其他费用)
    # but accepts custom values for edge cases. Limit 50 chars.
    category: Mapped[str] = mapped_column(String(50), nullable=False)

    # Native currency + amount. NEVER stored already-converted — we
    # always re-convert on read to honour daily FX changes (per goal).
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
