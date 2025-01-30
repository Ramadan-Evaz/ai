import copy
from datetime import datetime
from typing import Any, Literal

import httpx

from evazan_ai.agents.evazan_ai import Evazan AI
from evazan_ai.evazan_ai_db import Evazan AIDB, MessageLogger
from evazan_ai.evazan_ai_logger import get_logger
from evazan_ai.config import get_settings
from evazan_ai.util.general_helpers import get_language_from_text

logger = get_logger()

# Initialize the DB and agent
# TODO(odyash): A question for others: should I refer `db` of this file and `main_api.py` to a single instance of Evazan AIDB?
#    instead of duplicating `db` instances? Will this cost more resources?
db = Evazan AIDB(get_settings())


class WhatsAppPresenter:
    def __init__(
        self,
        agent: Evazan AI,
        access_token,
        business_phone_number_id,
        api_version="v21.0",
    ):
        self.agent = agent
        self.access_token = access_token
        self.meta_api_url = f"https://graph.facebook.com/{api_version}/{business_phone_number_id}/messages"

    async def extract_relevant_whatsapp_message_details(
        self,
        body: dict[str, Any],
    ) -> tuple[str, str, str] | str | None:
        """Extracts relevant whatsapp message details from the incoming webhook payload.

        Args:
            body (Dict[str, Any]): The JSON body of the incoming request.

        Returns:
            Optional[Tuple[str, str, str]]: A tuple containing the business phone number ID,
            the sender's WhatsApp number and the their message (if the extraction is successful).
            Returns None if the extraction fails.

        """
        # logger.debug(f"Received payload from WhatsApp user:\n{body}")

        if not (
            body.get("object")
            and (entry := body.get("entry", []))
            and (changes := entry[0].get("changes", []))
            and (value := changes[0].get("value", {}))
        ):
            error_msg = f"Invalid received payload from WhatsApp user and/or problem with Meta's API :\n{body}"
            logger.error(
                error_msg,
            )
            raise Exception(error_msg)

        if "statuses" in value:
            # status = value["statuses"]["status"]
            # timestamp = value["statuses"]["timestamp"]
            # # This log isn't important if we don't want to track when an Evazan AI's replied message is
            # # delivered to or read by the recipient
            # logger.debug(
            #     f"WhatsApp status update received:\n({status} at {timestamp}.)",
            # )
            return "status update"

        if "messages" not in value:
            error_msg = f"Unsupported message type received from WhatsApp user:\n{body}"
            logger.error(
                error_msg,
            )
            raise Exception(error_msg)

        incoming_msg = value["messages"][0]

        # Extract the phone number of the WhatsApp sender
        from_whatsapp_number = incoming_msg["from"]
        # Meta API note: Meta sends "errors" key when receiving unsupported message types
        # (e.g., video notes, gifs sent from giphy, or polls)
        incoming_msg_type = incoming_msg["type"] if incoming_msg["type"] in incoming_msg.keys() else "errors"
        # Extract the message of the WhatsApp sender (could be text, image, etc.)
        incoming_msg_body = incoming_msg[incoming_msg_type]

        logger.info(f"Received a supported whatsapp message from {from_whatsapp_number}: {incoming_msg_body}")

        return (
            from_whatsapp_number,
            incoming_msg_type,
            incoming_msg_body,
        )

    async def check_and_register_user(
        self,
        from_whatsapp_number: str,
        incoming_msg_type: str,
        incoming_msg_body: dict,
    ) -> None:
        """
        Checks if the user's phone number is stored in the users_whatsapp table.
        If not, registers the user with the preferred language.

        Args:
            from_whatsapp_number (str): The phone number of the WhatsApp sender.
            incoming_msg_type (str): The type of the incoming message (e.g., text, location).
            incoming_msg_body (dict): The body of the incoming message.

        Returns:
            None
        """
        # Check if the user's phone number is stored in users_whatsapp table
        if db.account_exists_whatsapp(phone_num=from_whatsapp_number):
            return True

        # Else, register the user with the detected language
        if incoming_msg_type == "text":
            incoming_msg_text = incoming_msg_body["body"]
            user_lang = get_language_from_text(incoming_msg_text)
        else:
            # TODO(odyash, good_first_issue): use lightweight library/solution that gives us language from country code
            # instead of hardcoding "en" in below code
            user_lang = "en"
        status: Literal["success", "failure"] = db.register_whatsapp(from_whatsapp_number, {"preferred_language": user_lang})[
            "status"
        ]
        if status == "success":
            logger.info(f"Registered new whatsapp user (lang: {user_lang})!: {from_whatsapp_number}")
            return True
        else:
            logger.error(f"Failed to register new whatsapp user: {from_whatsapp_number}")
            return False

    async def send_whatsapp_message(
        self,
        from_whatsapp_number: str,
        msg_body: str,
    ) -> None:
        """Sends a message to the WhatsApp sender.

        Args:
            from_whatsapp_number (str): The sender's WhatsApp number.
            msg_body (str): The message body to be sent.

        """
        url = self.meta_api_url
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        json_data = {
            "messaging_product": "whatsapp",
            "to": from_whatsapp_number,
            "text": {"body": msg_body},
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=json_data)
            response.raise_for_status()  # Raise an exception for HTTP errors
            if msg_body != "...":
                logger.info(
                    f"Evazan AI responsded to WhatsApp user: {from_whatsapp_number} with:\n{msg_body}",
                )

    async def handle_text_message(
        self,
        from_whatsapp_number: str,
        incoming_txt_msg: str,
    ) -> None:
        """Processes the incoming text message and sends a response to the WhatsApp sender.

        Args:
            from_whatsapp_number (str): The sender's WhatsApp number.
            incoming_txt_msg (str): The incoming text message from the sender.

        """
        try:
            logger.info(f"Whatsapp user said: {incoming_txt_msg}")

            # Get user's ID from users_whatsapp table
            # Note we're not checking for user's existence here, as we've already done that in `main_webhook()`
            user_id_whatsapp = db.retrieve_user_info_whatsapp(from_whatsapp_number, "id")[0]

            # Get details of thread with latest updated_at column
            thread_id, last_message_time = db.get_last_message_time_whatsapp(user_id_whatsapp)

            # Calculate the time passed since the last message
            if last_message_time is None:
                passed_time = float("inf")
            else:
                passed_time = (datetime.now() - last_message_time).total_seconds()

            # Log the time passed since the last message
            if passed_time < 60:
                passed_time_log = f"{passed_time:.1f}sec"
            elif passed_time < 3600:
                passed_time_log = f"{passed_time / 60:.1f}mins"
            elif passed_time < 86400:
                passed_time_log = f"{passed_time / 3600:.1f}hours"
            else:
                passed_time_log = f"{passed_time / 86400:.1f}days"
            logger.debug(f"Time passed since user ({user_id_whatsapp})'s last whatsapp message: {passed_time_log}mins")

            # Determine the allowed retention time
            if get_settings().DEBUG_MODE:
                reten_hours = 0.05  # so allowed_time == 3 minutes
            else:
                reten_hours = get_settings().WHATSAPP_CHAT_RETENTION_HOURS
            allowed_time = reten_hours * 60 * 60

            # Create a new thread if X hours have passed since last message
            if thread_id is None or passed_time > allowed_time:
                first_few_words = " ".join(incoming_txt_msg.split()[:6])
                thread_id = db.create_thread_whatsapp(user_id_whatsapp, first_few_words)
                logger.info(
                    f"Created a new thread for the whatsapp user ({user_id_whatsapp}), "
                    + "as the allowed retention time has passed."
                )

            # Store incoming message to current thread it's assigned to
            db.append_message_whatsapp(user_id_whatsapp, thread_id, {"role": "user", "content": incoming_txt_msg})

            # Get `message_history` from current thread (including incoming message)
            message_history = db.get_thread_llm_whatsapp(thread_id, user_id_whatsapp)
            message_history_for_debugging = [msg for msg in message_history if msg["role"] in {"user", "assistant"}]
            # Note: obviously, this log output won't consider Evazan AI's response, as it still happens later in the code below
            logger.debug(
                f"#msgs (user/assistant only) retrieved for user ({user_id_whatsapp})'s current whatsapp thread: "
                + str(len(message_history_for_debugging))
            )

            # Setting up `MessageLogger` for Evazan AI, so it can log (i.e., store) its response to the DB
            agent = copy.deepcopy(self.agent)
            agent.set_message_logger(MessageLogger(db, user_id_whatsapp, thread_id, to_whatsapp=True))

            # Get final response from Evazan AI by sending `message_history`
            # TODO(odyash, good_first_issue): change `stream` to False (and remove comprehensive loop)
            #   when `Evazan AI` is capable of handling it
            response = [tok for tok in agent.replace_message_history(message_history, stream=True) if tok]
            response = "".join(response)

            if response:
                await self.send_whatsapp_message(from_whatsapp_number, response)
            else:
                logger.warning("Response was empty. Sending error message.")
                await self.send_whatsapp_message(
                    from_whatsapp_number,
                    "Evazan AI returned an empty response. Please rephrase your question, then try again.",
                )
        except Exception as e:
            logger.error(f"Error processing message: {e}. Details are in next log.")
            logger.exception(e)
            await self.send_whatsapp_message(
                from_whatsapp_number,
                "An unexpected error occurred while processing your message. Please try again later.",
            )

    async def handle_location_message(
        self,
        from_whatsapp_number: str,
        incoming_msg_body: dict,
    ) -> None:
        """
        Handles an incoming location message by updating the user's location in the database
        and sending a confirmation message.

        Args:
            from_whatsapp_number (str): The phone number of the WhatsApp sender.
            incoming_msg_body (dict): The body of the incoming location message.

        Returns:
            None
        """
        loc = incoming_msg_body
        db.update_user_whatsapp(from_whatsapp_number, {"loc_lat": loc["latitude"], "loc_long": loc["longitude"]})
        # TODO(odyash, good_first_issue): update msg below to also say something like:
        # 'Type "pt"/"prayer times" to get prayer times', then implement that feature
        await self.send_whatsapp_message(
            from_whatsapp_number,
            "Stored your location successfully! This will help us give you accurate prayer times ISA 🙌.",
        )

    async def handle_unsupported_message(
        self,
        from_whatsapp_number: str,
        incoming_msg_type: str,
    ) -> None:
        """
        Handles an incoming unsupported message by sending an appropriate response.

        Args:
            from_whatsapp_number (str): The phone number of the WhatsApp sender.
            incoming_msg_type (str): The type of the incoming message (e.g., image, video).

        Returns:
            None
        """
        msg_type = incoming_msg_type + "s" if not incoming_msg_type.endswith("s") else incoming_msg_type
        msg_type = msg_type.replace("unsupporteds", "this media type")
        await self.send_whatsapp_message(
            from_whatsapp_number,
            f"Sorry, I can't process {msg_type} yet. Please send me a text message.",
        )

    def present(self):
        pass
