import asyncio
import sys
from claude_agent_sdk import query, ClaudeAgentOptions

country = sys.argv[1] if len(sys.argv) > 1 else "FR"

prompt = f"""
Work on country {country} for the OAM bronze project.

Sequence:
1. Build or update the country acquisition dossier from primary official sources.
2. If the dossier is already accepted, implement the connector changes.
3. Run relevant tests.
4. Return:
   - files changed
   - tests run
   - open risks
   - acceptance status
"""

async def main() -> None:
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Edit", "Write", "Bash", "Glob", "Grep", "WebSearch"],
            permission_mode="acceptEdits",
            setting_sources=["project"],
        ),
    ):
        if hasattr(message, "result"):
            print(message.result)

if __name__ == "__main__":
    asyncio.run(main())
