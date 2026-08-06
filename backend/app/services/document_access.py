"""נקודת הכניסה היחידה לבדיקת בעלות על Document (v0.9.0).

זו בדיוק הנקודה שכבר נכשלה 3 פעמים בעבר במערכת הזו (admin/employees,
employee/dashboard/{id}, simulate-exercise - ראו QA_TESTBOOK.md "P2"): רשימה
נבדקת נכון, אבל שליפה בודדת לפי ID לא נבדקת מספיק. לכן פונקציה אחת משותפת,
לא בדיקה מועתקת בכל route - כל endpoint שמחזיר/מוריד מסמך חייב לקרוא לה
ראשון, בדיוק כמו _assert_ledger_ownership ב-routes.py.

הבדיקה היא תמיד השוואת עמודה ישירה על שורת Document עצמה (company_id/
employee_id/trustee_id, מוכפלים שם בכוונה) - לא join דרך grant_id, כדי
שהבדיקה לא תסמוך בשקט על זה ש-grant_id באמת שייך למי שהוא אמור להיות שייך.
"""

from fastapi import HTTPException

from backend.app.models import Document, User, UserRole


def assert_document_access(document: Document, current_user: User) -> None:
    if current_user.role == UserRole.COMPANY_ADMIN:
        if document.company_id != current_user.company_id:
            raise HTTPException(status_code=403, detail="This document does not belong to your company")
        return

    if current_user.role == UserRole.EMPLOYEE:
        if document.employee_id != current_user.employee_id:
            raise HTTPException(status_code=403, detail="This document does not belong to you")
        return

    if current_user.role == UserRole.TRUSTEE:
        if document.trustee_id != current_user.trustee_id:
            raise HTTPException(status_code=403, detail="This document is not linked to a grant you hold in trust")
        return

    raise HTTPException(status_code=403, detail="Role not permitted to access documents")
