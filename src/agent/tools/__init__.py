from agent.tools.earnings import EARNINGS_TOOL, get_earnings_calendar
from agent.tools.estimates import ESTIMATES_TOOL, get_analyst_estimates
from agent.tools.news import NEWS_TOOL, get_stock_news

ALL_TOOL_SCHEMAS = [
    EARNINGS_TOOL,
    ESTIMATES_TOOL,
    NEWS_TOOL,
]

TOOL_FUNCTIONS = {
    "get_earnings_calendar": get_earnings_calendar,
    "get_analyst_estimates": get_analyst_estimates,
    "get_stock_news": get_stock_news,
}
