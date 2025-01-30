import logging

from evazan_ai.agents import Evazan AI
from evazan_ai.config import get_settings
from evazan_ai.presenters.stdio_presenter import StdioPresenter

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    agent = Evazan AI(get_settings())
    presenter = StdioPresenter(agent)
    presenter.present()
