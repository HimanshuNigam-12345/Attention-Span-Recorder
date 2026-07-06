import sys
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("attention-span-recorder")

@mcp.tool()
def get_distraction_warning(app_name: str) -> str:
    """Checks if the application is categorized as distracting and returns a warning.

    Args:
        app_name: The name of the application to check (e.g. YouTube, Instagram).
    """
    distracting = ["instagram", "youtube", "facebook", "snapchat", "tiktok", "games"]
    if app_name.lower() in distracting:
        return f"🚨 Distraction Alert: '{app_name}' is classified as a distracting app. Accessing it will impact your daily focus streak!"
    return f"ℹ️ Info: '{app_name}' is not marked as distracting. You can continue using it normally."

@mcp.tool()
def get_productivity_tips(level: int) -> str:
    """Provides productivity tips tailored to the user's gamification level.

    Args:
        level: The current level of the user (e.g. 1, 2, 3+).
    """
    tips = {
        1: "Focus Tip: Start with a 25-minute Pomodoro session. Put your phone on silent and face down.",
        2: "Focus Tip: Batch your distractions! Allocate a 10-minute break after every 50 minutes of deep study.",
        3: "Focus Tip: Amazing progress! Try a 45-minute deep focus block today to train your concentration muscle."
    }
    return tips.get(level, tips[1])

@mcp.tool()
def log_study_notes(session_notes: str) -> str:
    """Logs study notes or reflections recorded by the user after completing a focus session.

    Args:
        session_notes: The content of the notes or reflections.
    """
    return f"📝 Logged study notes: '{session_notes}'."

if __name__ == "__main__":
    mcp.run()
