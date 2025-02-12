import logging
import os
import uuid

import psycopg2
import psycopg2.extras
from diskcache import FanoutCache, Lock
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from jinja2 import Environment, FileSystemLoader
from langfuse.decorators import langfuse_context, observe
from pydantic import BaseModel
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from starlette.exceptions import HTTPException as StarletteHTTPException
from zxcvbn import zxcvbn

from src.ansari.agents import EvazanAI, EvazanAIWorkflow
from src.ansari.evazan_ai_db import EvazanAIDB, MessageLogger
from src.ansari.evazan_ai_logger import get_logger
from src.ansari.app.main_whatsapp import router as whatsapp_router
from src.ansari.config import Settings, get_settings
from src.ansari.presenters.api_presenter import ApiPresenter
from src.ansari.util.general_helpers import get_extended_origins, validate_cors

logger = get_logger()

# Register the UUID type globally
psycopg2.extras.register_uuid()

app = FastAPI()


# Custom exception handler, which aims to log FastAPI-related exceptions before raising them
# Details: https://fastapi.tiangolo.com/tutorial/handling-errors/#override-request-validation-exceptions
#   Side note: apparently, there's no need to write another `RequestValidationError`-related function,
#   contrary to what's mentioned in the above URL.
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc: HTTPException):
    logger.error(f"{exc}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


def add_app_middleware():
    origins = get_extended_origins()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


add_app_middleware()

db = EvazanAIDB(get_settings())
evazan_ai = EvazanAI(get_settings())

presenter = ApiPresenter(app, evazan_ai)
presenter.present()

cache = FanoutCache(get_settings().diskcache_dir, shards=4, timeout=1)

# Include the WhatsApp router
app.include_router(whatsapp_router)

if __name__ == "__main__" and get_settings().DEBUG_MODE:
    # Programatically start a Uvicorn server while debugging (development) for easier control/accessibility
    #   I.e., just run:
    #   `python src/evazan_ai/app/main_api.py`
    # Note 1: if you instead run
    #   `uvicorn main_api:app --host YOUR_HOST --port YOUR_PORT`
    # in the terminal, then this `if __name__ ...` block will be ignored

    # Note 2: you have to use zrok to test whatsapp's webhook locally,
    # Check the resources at `.env.example` file for more details, but TL;DR:
    # Run 3 commands below:
    # Only run on initial setup (if error occurs, contact odyash on GitHub):
    #   `zrok enable SECRET_TOKEN_GENERATED_BY_ZROK_FOR_YOUR_DEVICE`
    #   `zrok reserve public localhost:8000 -n ZROK_SHARE_TOKEN`
    # Run on initial setup and upon starting a new terminal session:
    #   `zrok share reserved ZROK_SHARE_TOKEN`
    import uvicorn

    filename_without_extension = os.path.splitext(os.path.basename(__file__))[0]
    if __name__ == "__main__":
    uvicorn.run("src.ansari.app.main_api:app", host="0.0.0.0", port=8000, reload=True)
        f"{filename_without_extension}:app",
        host="localhost",
        port=8000,
        reload=True,
        log_level="debug",
    )


class RegisterRequest(BaseModel):
    email: str
    password: str
    first_name: str
    last_name: str


@app.post("/api/v2/users/register")
async def register_user(req: RegisterRequest, cors_ok: bool = Depends(validate_cors)):
    """Register a new user.
    If the user exists, returns 403.
    Returns 200 on success.
    Returns 400 if the password is too weak. Will include suggestions for a stronger password.
    """
    if not cors_ok:
        raise HTTPException(status_code=403, detail="CORS not permitted")

    password_hash = db.hash_password(req.password)
    logger.info(
        f"Received request to create account: {req.email} {password_hash} {req.first_name} {req.last_name}",
    )
    try:
        # Check if account exists
        if db.account_exists(req.email):
            raise HTTPException(status_code=403, detail="Account already exists")
        passwd_quality = zxcvbn(req.password)
        if passwd_quality["score"] < 2:
            raise HTTPException(
                status_code=400,
                detail="Password is too weak. Suggestions: " + ",".join(passwd_quality["feedback"]["suggestions"]),
            )
        return db.register(req.email, req.first_name, req.last_name, password_hash)
    except psycopg2.Error as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Database error")


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/api/v2/users/login")
async def login_user(
    req: LoginRequest,
    cors_ok: bool = Depends(validate_cors),
    settings: Settings = Depends(get_settings),
):
    """Logs the user in.
    Returns a token on success.
    Returns 403 if the password is incorrect or the user doesn't exist.
    """
    if not cors_ok:
        raise HTTPException(status_code=403, detail="CORS not permitted")

    if not db.account_exists(req.email):
        raise HTTPException(status_code=403, detail="Invalid username or password")

    user_id, existing_hash, first_name, last_name = db.retrieve_user_info(req.email)

    if not db.check_password(req.password, existing_hash):
        raise HTTPException(status_code=403, detail="Invalid username or password")

    # Generate a token and return it
    try:
        access_token = db.generate_token(
            user_id,
            token_type="access",
            expiry_hours=settings.ACCESS_TOKEN_EXPIRY_HOURS,
        )
        refresh_token = db.generate_token(
            user_id,
            token_type="refresh",
            expiry_hours=settings.REFRESH_TOKEN_EXPIRY_HOURS,
        )
        access_token_insert_result = db.save_access_token(user_id, access_token)
        if access_token_insert_result["status"] != "success":
            raise HTTPException(
                status_code=500,
                detail="Couldn't save access token",
            )
        refresh_token_insert_result = db.save_refresh_token(
            user_id,
            refresh_token,
            access_token_insert_result["token_db_id"],
        )
        if refresh_token_insert_result["status"] != "success":
            raise HTTPException(
                status_code=500,
                detail="Couldn't save refresh token",
            )
        return {
            "status": "success",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "first_name": first_name,
            "last_name": last_name,
        }
    except psycopg2.Error as e:
        logger.critical(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Database error")


@app.post("/api/v2/users/refresh_token")
async def refresh_token(
    request: Request,
    cors_ok: bool = Depends(validate_cors),
    settings: Settings = Depends(get_settings),
):
    """Refresh both the access token and the refresh token.

    Returns:
        dict: A dictionary containing the new access and refresh tokens on success.

    Raises:
        HTTPException:
            - 403 if CORS validation fails or the token type is invalid.
            - 401 if the refresh token is invalid or has expired.
            - 500 if there is an internal server error during token generation or saving.

    """
    if not cors_ok:
        raise HTTPException(status_code=403, detail="CORS not permitted")

    old_refresh_token = request.headers.get("Authorization", "").split(" ")[1]
    token_params = db.decode_token(old_refresh_token)

    lock_key = f"lock:{token_params['user_id']}"
    with Lock(cache, lock_key, expire=3):
        # Check cache for existing token pair
        cached_tokens = cache.get(old_refresh_token)
        if cached_tokens:
            return {"status": "success", **cached_tokens}

        # If no cached tokens, proceed to validate and generate new tokens
        try:
            # Validate the refresh token and delete the old token pair
            db.delete_access_refresh_tokens_pair(old_refresh_token)

            # Generate new tokens
            new_access_token = db.generate_token(
                token_params["user_id"],
                token_type="access",
                expiry_hours=settings.ACCESS_TOKEN_EXPIRY_HOURS,
            )
            new_refresh_token = db.generate_token(
                token_params["user_id"],
                token_type="refresh",
                expiry_hours=settings.REFRESH_TOKEN_EXPIRY_HOURS,
            )

            # Save the new access token to the database
            access_token_insert_result = db.save_access_token(
                token_params["user_id"],
                new_access_token,
            )
            if access_token_insert_result["status"] != "success":
                raise HTTPException(
                    status_code=500,
                    detail="Couldn't save access token",
                )

            # Save the new refresh token to the database
            refresh_token_insert_result = db.save_refresh_token(
                token_params["user_id"],
                new_refresh_token,
                access_token_insert_result["token_db_id"],
            )
            if refresh_token_insert_result["status"] != "success":
                raise HTTPException(
                    status_code=500,
                    detail="Couldn't save refresh token",
                )

            # Cache the new tokens with a short expiry (3 seconds)
            new_tokens = {
                "access_token": new_access_token,
                "refresh_token": new_refresh_token,
            }
            cache.set(old_refresh_token, new_tokens, expire=3)
            return {"status": "success", **new_tokens}
        except psycopg2.Error as e:
            logger.critical(f"Error: {e}")
            raise HTTPException(status_code=500, detail="Database error")


@app.post("/api/v2/users/logout")
async def logout_user(
    request: Request,
    cors_ok: bool = Depends(validate_cors),
    token_params: dict = Depends(db.validate_token),
):
    """Logs the user out.
    Deletes all tokens.
    Returns 403 if the password is incorrect or the user doesn't exist.
    """
    if not (cors_ok and token_params):
        raise HTTPException(status_code=403, detail="Invalid username or password")

    try:
        token = request.headers.get("Authorization", "").split(" ")[1]
        db.logout(token_params["user_id"], token)
        return {"status": "success"}
    except psycopg2.Error as e:
        logger.critical(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Database error")


class FeedbackRequest(BaseModel):
    thread_id: uuid.UUID
    message_id: int
    feedback_class: str
    comment: str


@app.post("/api/v2/feedback")
async def add_feedback(
    req: FeedbackRequest,
    cors_ok: bool = Depends(validate_cors),
    token_params: dict = Depends(db.validate_token),
):
    if not (cors_ok and token_params):
        raise HTTPException(status_code=403, detail="CORS not permitted")

    logger.info(f"Token_params is {token_params}")
    # Now create a thread and return the thread_id
    try:
        db.add_feedback(
            token_params["user_id"],
            req.thread_id,
            req.message_id,
            req.feedback_class,
            req.comment,
        )
        return {"status": "success"}
    except psycopg2.Error as e:
        logger.critical(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Database error")


@app.post("/api/v2/threads")
async def create_thread(
    request: Request,
    cors_ok: bool = Depends(validate_cors),
    token_params: dict = Depends(db.validate_token),
):
    if not (cors_ok and token_params):
        raise HTTPException(status_code=403, detail="CORS not permitted")

    logger.info(f"Token_params is {token_params}")
    # Now create a thread and return the thread_id
    try:
        thread_id = db.create_thread(token_params["user_id"])
        print(f"Created thread {thread_id}")
        return thread_id
    except psycopg2.Error as e:
        logger.critical(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Database error")


@app.get("/api/v2/threads")
async def get_all_threads(
    request: Request,
    cors_ok: bool = Depends(validate_cors),
    token_params: dict = Depends(db.validate_token),
):
    """Retrieve all threads for the user whose id is included in the token."""
    if not (cors_ok and token_params):
        raise HTTPException(status_code=403, detail="CORS not permitted")

    logger.info(f"Token_params is {token_params}")
    # Now create a thread and return the thread_id
    try:
        threads = db.get_all_threads(token_params["user_id"])
        return threads
    except psycopg2.Error as e:
        logger.critical(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Database error")


class AddMessageRequest(BaseModel):
    role: str
    content: str


@app.post("/api/v2/threads/{thread_id}")
@observe(capture_output=False)
def add_message(
    thread_id: uuid.UUID,
    req: AddMessageRequest,
    cors_ok: bool = Depends(validate_cors),
    token_params: dict = Depends(db.validate_token),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Adds a message to a thread. If the message is the first message in the thread,
    we set the name of the thread to the content of the message.
    """
    if not (cors_ok and token_params):
        raise HTTPException(status_code=403, detail="CORS not permitted")

    logger.info(f"Token_params is {token_params}")

    try:
        db.append_message(token_params["user_id"], thread_id, req.role, req.content)
        # Now actually use EvazanAI.
        history = db.get_thread_llm(thread_id, token_params["user_id"])
        if history["thread_name"] is None and len(history["messages"]) > 1:
            db.set_thread_name(
                thread_id,
                token_params["user_id"],
                history["messages"][0]["content"],
            )
            logger.info(f"Added thread {thread_id}")

        langfuse_context.update_current_trace(
            session_id=str(thread_id),
            user_id=token_params["user_id"],
            tags=["debug"],
            metadata={
                "db_host": settings.DATABASE_URL.hosts()[0]["host"],
            },
        )
        return presenter.complete(
            history,
            message_logger=MessageLogger(
                db,
                token_params["user_id"],
                thread_id,
                langfuse_context.get_current_trace_id(),
            ),
        )
    except psycopg2.Error as e:
        logger.critical(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Database error")


@app.post("/api/v2/share/{thread_id}")
def share_thread(
    thread_id: uuid.UUID,
    cors_ok: bool = Depends(validate_cors),
    token_params: dict = Depends(db.validate_token),
):
    """Take a snapshot of a thread at this time and make it shareable."""
    if not (cors_ok and token_params):
        raise HTTPException(status_code=403, detail="CORS not permitted")

    logger.info(f"Token_params is {token_params}")
    # TODO(mwk): check that the user_id in the token matches the
    # user_id associated with the thread_id.
    try:
        share_uuid = db.snapshot_thread(thread_id, token_params["user_id"])
        return {"status": "success", "share_uuid": share_uuid}
    except psycopg2.Error as e:
        logger.critical(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Database error")


@app.get("/api/v2/share/{share_uuid_str}")
def get_snapshot(
    share_uuid_str: str,
    cors_ok: bool = Depends(validate_cors),
):
    """Take a snapshot of a thread at this time and make it shareable."""
    if not cors_ok:
        raise HTTPException(status_code=403, detail="CORS not permitted")

    logger.info(f"Incoming share_uuid is {share_uuid_str}")
    share_uuid = uuid.UUID(share_uuid_str)
    try:
        content = db.get_snapshot(share_uuid)
        return {"status": "success", "content": content}
    except psycopg2.Error as e:
        logger.critical(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Database error")


@app.get("/api/v2/threads/{thread_id}")
async def get_thread(
    thread_id: uuid.UUID,
    cors_ok: bool = Depends(validate_cors),
    token_params: dict = Depends(db.validate_token),
):
    if not (cors_ok and token_params):
        raise HTTPException(status_code=403, detail="CORS not permitted")

    logger.info(f"Token_params is {token_params}")
    # TODO(mwk): check that the user_id in the token matches the
    # user_id associated with the thread_id.
    try:
        messages = db.get_thread(thread_id, token_params["user_id"])
        if messages:  # return only if the thread exists. else raise 404
            return messages
        raise HTTPException(status_code=404, detail="Thread not found")
    except psycopg2.Error as e:
        logger.critical(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Database error")


@app.delete("/api/v2/threads/{thread_id}")
async def delete_thread(
    thread_id: uuid.UUID,
    cors_ok: bool = Depends(validate_cors),
    token_params: dict = Depends(db.validate_token),
):
    if not (cors_ok and token_params):
        raise HTTPException(status_code=403, detail="CORS not permitted")

    logger.info(f"Token_params is {token_params}")
    # TODO(mwk): check that the user_id in the token matches the
    # user_id associated with the thread_id.
    try:
        return db.delete_thread(thread_id, token_params["user_id"])
    except psycopg2.Error as e:
        logger.critical(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Database error")


class ThreadNameRequest(BaseModel):
    name: str


@app.post("/api/v2/threads/{thread_id}/name")
async def set_thread_name(
    thread_id: uuid.UUID,
    req: ThreadNameRequest,
    cors_ok: bool = Depends(validate_cors),
    token_params: dict = Depends(db.validate_token),
):
    if not (cors_ok and token_params):
        raise HTTPException(status_code=403, detail="CORS not permitted")

    logger.info(f"Token_params is {token_params}")
    # TODO(mwk): check that the user_id in the token matches the
    # user_id associated with the thread_id.
    try:
        messages = db.set_thread_name(thread_id, token_params["user_id"], req.name)
        return messages
    except psycopg2.Error as e:
        logger.critical(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Database error")


class SetPrefRequest(BaseModel):
    key: str
    value: str


@app.post("/api/v2/preferences")
async def set_pref(
    req: SetPrefRequest,
    cors_ok: bool = Depends(validate_cors),
    token_params: dict = Depends(db.validate_token),
):
    if not (cors_ok and token_params):
        raise HTTPException(status_code=403, detail="CORS not permitted")

    logger.info(f"Token_params is {token_params}")
    # Now create a thread and return the thread_id
    try:
        db.set_pref(token_params["user_id"], req.key, req.value)
    except psycopg2.Error as e:
        logger.critical(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Database error")


@app.get("/api/v2/preferences")
async def get_prefs(
    cors_ok: bool = Depends(validate_cors),
    token_params: dict = Depends(db.validate_token),
):
    if not (cors_ok and token_params):
        raise HTTPException(status_code=403, detail="CORS not permitted")

    logger.info(f"Token_params is {token_params}")
    # Now create a thread and return the thread_id
    try:
        prefs = db.get_prefs(token_params["user_id"])
        return prefs
    except psycopg2.Error as e:
        logger.critical(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Database error")


class ResetPasswordRequest(BaseModel):
    email: str


@app.post("/api/v2/request_password_reset")
async def request_password_reset(
    req: ResetPasswordRequest,
    cors_ok: bool = Depends(validate_cors),
    settings: Settings = Depends(get_settings),
):
    if not cors_ok:
        raise HTTPException(status_code=403, detail="CORS not permitted")

    logger.info(f"Request received to reset {req.email}")
    if db.account_exists(req.email):
        user_id, _, _, _ = db.retrieve_user_info(req.email)
        reset_token = db.generate_token(user_id, "reset")
        db.save_reset_token(user_id, reset_token)
        # shall we also revoke login and refresh tokens?
        tenv = Environment(loader=FileSystemLoader(settings.template_dir))
        template = tenv.get_template("password_reset.html")
        rendered_template = template.render(reset_token=reset_token)
        message = Mail(
            from_email="feedback@evazan_ai.chat",
            to_emails=f"{req.email}",
            subject="EvazanAI Password Reset",
            html_content=rendered_template,
        )

        try:
            if settings.SENDGRID_API_KEY:
                sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
                response = sg.send(message)
                logger.debug(response.status_code)
                logger.debug(response.body)
                logger.debug(response.headers)
            else:
                logger.warning("No sendgrid key")
                logger.info(f"Would have sent: {message}")
        except Exception as e:
            print(e.message)
    # Even if the email doesn't exist, we return success.
    # So this can't be used to work out who is on our system.
    return {"status": "success"}


@app.post("/api/v2/update_password")
async def update_password(
    cors_ok: bool = Depends(validate_cors),
    token_params: dict = Depends(db.validate_reset_token),
    password: str = None,
):
    """Update the user's password if you have a valid token"""
    if not (cors_ok and token_params):
        raise HTTPException(status_code=403, detail="Invalid username or password")

    logger.info(f"Token_params is {token_params}")
    try:
        password_hash = db.hash_password(password)
        passwd_quality = zxcvbn(password)
        if passwd_quality["score"] < 2:
            raise HTTPException(
                status_code=400,
                detail="Password is too weak. Suggestions: " + ",".join(passwd_quality["feedback"]["suggestions"]),
            )
        db.update_password(token_params["email"], password_hash)
    except psycopg2.Error as e:
        logger.critical(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Database error")


class PasswordReset(BaseModel):
    reset_token: str
    new_password: str


@app.post("/api/v2/reset_password")
async def reset_password(req: PasswordReset, cors_ok: bool = Depends(validate_cors)):
    """Resets the user's password if you have a reset token."""
    token_params = db.validate_reset_token(req.reset_token)
    if not cors_ok:
        raise HTTPException(status_code=403, detail="Invalid username or password")

    logger.info(f"Token_params is {token_params}")
    try:
        password_hash = db.hash_password(req.new_password)
        passwd_quality = zxcvbn(req.new_password)
        if passwd_quality["score"] < 2:
            raise HTTPException(
                status_code=400,
                detail="Password is too weak. Suggestions: " + ",".join(passwd_quality["feedback"]["suggestions"]),
            )
        db.update_password(token_params["user_id"], password_hash)
        return {"status": "success"}
    except psycopg2.Error as e:
        logger.critical(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Database error")


@app.post("/api/v1/complete")
async def complete(request: Request, cors_ok: bool = Depends(validate_cors)):
    """Provides a response to a user's input.
    The input is a list of messages, each with with
    a role and a text field. Roles are typically
    'user' or 'assistant.' The client should maintain the
    record of the conversation client side.

    It returns a stream of tokens (a token is a part of a word).

    """
    if not cors_ok:
        raise HTTPException(status_code=403, detail="CORS not permitted")

    logger.debug(f"Raw request is {request.headers}")
    body = await request.json()
    logger.info(f"Request received > {body}.")
    return presenter.complete(body)


class AyahQuestionRequest(BaseModel):
    surah: int
    ayah: int
    question: str
    augment_question: bool | None = False
    use_cache: bool | None = True
    apikey: str


@app.post("/api/v2/ayah")
async def answer_ayah_question(
    req: AyahQuestionRequest,
    cors_ok: bool = Depends(validate_cors),
    settings: Settings = Depends(get_settings),
    db: EvazanAIDB = Depends(lambda: EvazanAIDB(get_settings())),
):
    if not cors_ok:
        raise HTTPException(status_code=403, detail="CORS not permitted")

    if req.apikey != settings.QURAN_DOT_COM_API_KEY.get_secret_value():
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        # Create EvazanAIWorkflow instance with ayah-specific system prompt
        logging.debug(f"Creating EvazanAI Workflow instance for {req.surah}:{req.ayah}")
        evazan_ai_workflow = EvazanAIWorkflow(settings, system_prompt_file=settings.AYAH_SYSTEM_PROMPT_FILE_NAME)

        ayah_id = req.surah * 1000 + req.ayah

        # Check if the answer is already stored in the database
        if req.use_cache:
            stored_answer = db.get_quran_answer(req.surah, req.ayah, req.question)
            if stored_answer:
                return {"response": stored_answer}

        # Define the workflow steps
        workflow_steps = [
            (
                "search",
                {
                    "query": req.question,
                    "tool_name": "search_tafsir",
                    "metadata_filter": f"part.from_ayah_int<={ayah_id} AND part.to_ayah_int>={ayah_id}",
                },
            ),
            ("gen_query", {"input": req.question, "target_corpus": "tafsir"}),
            ("gen_answer", {"input": req.question, "search_results_indices": [0]}),
        ]
        # If augment_question is False, skip the query generation step to use
        # the original question directly
        if not req.augment_question:
            workflow_steps.pop(1)

        # Execute the workflow
        workflow_output = evazan_ai_workflow.execute_workflow(workflow_steps)

        # The answer is the last item in the workflow output
        evazan_ai_answer = workflow_output[-1]

        # Store the answer in the database
        db.store_quran_answer(req.surah, req.ayah, req.question, evazan_ai_answer)

        return {"response": evazan_ai_answer}
    except Exception:
        logger.error("Error in answer_ayah_question", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
