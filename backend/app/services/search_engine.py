import difflib

from sqlalchemy.orm import Session

from backend.app.models import Employee, Grant, ExerciseRequest, OptionPool, AuditLog, User


def _score(query: str, *fields) -> float:
    """ניקוד דמיון טקסטואלי דטרמיניסטי - difflib בלבד (ספריית סטנדרט של
    פייתון), בלי שום מודל שפה/שירות חיצוני. אותו קלט תמיד מייצר אותו ניקוד."""
    query = (query or "").strip().lower()
    if not query:
        return 0.0
    best = 0.0
    for field in fields:
        if not field:
            continue
        field = str(field).lower()
        if query in field:
            best = max(best, 0.9 + 0.1 * (len(query) / max(len(field), 1)))
        else:
            best = max(best, difflib.SequenceMatcher(None, query, field).ratio())
    return best


_MIN_SCORE = 0.35
_MAX_RESULTS = 30


class SearchResult:
    def __init__(self, entity_type, entity_id, title, subtitle, score):
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.title = title
        self.subtitle = subtitle
        self.score = score


def _rank(items):
    items = [i for i in items if i.score >= _MIN_SCORE]
    items.sort(key=lambda i: i.score, reverse=True)
    return items[:_MAX_RESULTS]


class SearchEngine:
    """חיפוש חכם מקומי (fuzzy, סובלני לשגיאות הקלדה) - בלי LLM, בלי תלות
    חיצונית, בלי עלות. כל פונקציה כאן מוגבלת מראש לתחום ההרשאה של המשתמש
    (לא חוזרת על הבאג המכוון של list_employees שרואה הכל)."""

    @staticmethod
    def search_for_admin(db: Session, company_id: str, query: str):
        results = []

        for emp in db.query(Employee).filter(Employee.company_id == company_id).all():
            status = emp.status.value if hasattr(emp.status, "value") else emp.status
            s = _score(query, emp.employee_id, emp.first_name, emp.last_name, emp.email, status)
            if s:
                results.append(SearchResult("Employee", emp.employee_id,
                                             f"{emp.first_name} {emp.last_name}", f"{emp.email} - {status}", s))

        pool_ids = [p.pool_id for p in db.query(OptionPool.pool_id).filter(OptionPool.company_id == company_id).all()]
        grants = db.query(Grant).filter(Grant.pool_id.in_(pool_ids)).all()
        for g in grants:
            gtype = g.grant_type.value if hasattr(g.grant_type, "value") else g.grant_type
            s = _score(query, g.grant_id, gtype, g.pool_id, g.employee_id)
            if s:
                results.append(SearchResult("Grant", g.grant_id, g.grant_id,
                                             f"{gtype} - {g.total_options:.0f} אופציות", s))

        grant_ids = [g.grant_id for g in grants]
        for r in db.query(ExerciseRequest).filter(ExerciseRequest.grant_id.in_(grant_ids)).all():
            rstatus = r.status.value if hasattr(r.status, "value") else r.status
            s = _score(query, r.request_id, r.grant_id, r.employee_id, rstatus)
            if s:
                results.append(SearchResult("ExerciseRequest", r.request_id, r.request_id,
                                             f"{r.options_requested:.0f} אופציות - {rstatus}", s))

        owned_audit_ids = {"Employee": {e.employee_id for e in db.query(Employee.employee_id).filter(Employee.company_id == company_id).all()},
                           "Grant": set(grant_ids), "TaxSimulation": set(grant_ids),
                           "ExerciseRequest": {r.request_id for r in db.query(ExerciseRequest.request_id).filter(ExerciseRequest.grant_id.in_(grant_ids)).all()},
                           "Company": {company_id}}
        for row in db.query(AuditLog).all():
            if row.entity_id not in owned_audit_ids.get(row.entity_type, set()):
                continue
            s = _score(query, row.entity_type, row.entity_id, row.action, row.notes)
            if s:
                results.append(SearchResult("AuditLog", row.audit_id, f"{row.entity_type}: {row.action}",
                                             f"{row.entity_id} - {row.occurred_at}", s))

        return _rank(results)

    @staticmethod
    def search_for_trustee(db: Session, trustee_id: str, query: str):
        results = []
        grants = db.query(Grant).filter(Grant.trustee_id == trustee_id).all()
        grant_ids = [g.grant_id for g in grants]
        employee_ids = list({g.employee_id for g in grants})

        for g in grants:
            emp = g.employee
            gtype = g.grant_type.value if hasattr(g.grant_type, "value") else g.grant_type
            s = _score(query, g.grant_id, gtype, emp.first_name if emp else None, emp.last_name if emp else None,
                       emp.company.name if (emp and emp.company) else None)
            if s:
                results.append(SearchResult("Grant", g.grant_id,
                                             f"{g.grant_id} ({emp.first_name + ' ' + emp.last_name if emp else '-'})",
                                             f"{gtype} - {(emp.company.name if emp and emp.company else '-')}", s))

        for r in db.query(ExerciseRequest).filter(ExerciseRequest.grant_id.in_(grant_ids)).all():
            rstatus = r.status.value if hasattr(r.status, "value") else r.status
            s = _score(query, r.request_id, r.grant_id, r.employee_id, rstatus)
            if s:
                results.append(SearchResult("ExerciseRequest", r.request_id, r.request_id,
                                             f"{r.options_requested:.0f} אופציות - {rstatus}", s))

        owned_audit_ids = {"Grant": set(grant_ids), "TaxSimulation": set(grant_ids),
                           "Employee": set(employee_ids),
                           "ExerciseRequest": {r.request_id for r in db.query(ExerciseRequest.request_id).filter(ExerciseRequest.grant_id.in_(grant_ids)).all()}}
        for row in db.query(AuditLog).all():
            if row.entity_id not in owned_audit_ids.get(row.entity_type, set()):
                continue
            s = _score(query, row.entity_type, row.entity_id, row.action, row.notes)
            if s:
                results.append(SearchResult("AuditLog", row.audit_id, f"{row.entity_type}: {row.action}",
                                             f"{row.entity_id} - {row.occurred_at}", s))

        return _rank(results)

    @staticmethod
    def search_for_employee(db: Session, employee_id: str, query: str):
        results = []
        grants = db.query(Grant).filter(Grant.employee_id == employee_id).all()
        grant_ids = [g.grant_id for g in grants]

        for g in grants:
            gtype = g.grant_type.value if hasattr(g.grant_type, "value") else g.grant_type
            s = _score(query, g.grant_id, gtype)
            if s:
                results.append(SearchResult("Grant", g.grant_id, g.grant_id,
                                             f"{gtype} - {g.total_options:.0f} אופציות", s))

        for r in db.query(ExerciseRequest).filter(ExerciseRequest.employee_id == employee_id).all():
            rstatus = r.status.value if hasattr(r.status, "value") else r.status
            s = _score(query, r.request_id, r.grant_id, rstatus)
            if s:
                results.append(SearchResult("ExerciseRequest", r.request_id, r.request_id,
                                             f"{r.options_requested:.0f} אופציות - {rstatus}", s))

        request_ids = {r.request_id for r in db.query(ExerciseRequest.request_id).filter(ExerciseRequest.employee_id == employee_id).all()}
        owned_audit_ids = {"Employee": {employee_id}, "Grant": set(grant_ids), "TaxSimulation": set(grant_ids), "ExerciseRequest": request_ids}
        for row in db.query(AuditLog).all():
            if row.entity_id not in owned_audit_ids.get(row.entity_type, set()):
                continue
            s = _score(query, row.entity_type, row.entity_id, row.action, row.notes)
            if s:
                results.append(SearchResult("AuditLog", row.audit_id, f"{row.entity_type}: {row.action}",
                                             f"{row.entity_id} - {row.occurred_at}", s))

        return _rank(results)
