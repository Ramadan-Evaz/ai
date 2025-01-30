from evazan_ai.agents import Evazan AI
from evazan_ai.config import get_settings
from presenters.discord_presenter import DiscordPresenter

# This work involves 3 agents, with Evazan AI as primary.
agent = Evazan AI(get_settings())
presenter = DiscordPresenter(
    agent,
    token=get_settings().DISCORD_TOKEN.get_secret_value(),
)

# This starts the UI.
presenter.present()
