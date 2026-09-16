"""Product matching pipeline.

Three stages, each deterministic and independently testable:
  1. `geo` — resolve country_id/port_id/delivery_date from Order metadata
  2. `code_first` — exact product-code match against the masterdata Products table
  3. `llm_refine` — fuzzy match for anything code_first missed

Entry point: `service.run_matching(order, db)`.
"""

from domains.orders.matching.service import run_matching

__all__ = ["run_matching"]
