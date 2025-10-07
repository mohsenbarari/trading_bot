from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routers import invitations, auth

app = FastAPI(title="Trading Bot API")

# CORS Middleware
origins = [
    "http://localhost:3000",    # For local web development
    "http://localhost:8080",    # Another common local port
    # Add your production frontend URLs here
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(invitations.router)
app.include_router(auth.router)