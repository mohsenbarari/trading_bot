import secrets
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from schemas import Invitation, InvitationCreate
from models.invitation import Invitation as InvitationModel
from core.db import get_db

app = FastAPI(title="Trading Bot API")

@app.post("/invitations/", response_model=Invitation, status_code=201)
async def create_invitation(invitation: InvitationCreate, db: AsyncSession = Depends(get_db)):
    stmt = select(InvitationModel).where(InvitationModel.mobile_number == invitation.mobile_number)
    if (await db.execute(stmt)).scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Invitation for this mobile number already exists.")

    token = f"INV-{secrets.token_hex(16)}"
    db_invitation = InvitationModel(mobile_number=invitation.mobile_number, token=token)
    db.add(db_invitation)
    await db.commit()
    await db.refresh(db_invitation)
    return db_invitation