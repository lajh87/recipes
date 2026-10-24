"""Local MCP entrypoint: python -m app.mcp_server."""
from mcp.server.fastmcp import FastMCP
from app.recipe_tools import (
    PlannedMeal, search_recipes, get_recipe, get_meal_plan,
    create_meal_plan, update_meal_plan,
)

mcp = FastMCP("Heley Family Cookbook")
for tool in (search_recipes, get_recipe, get_meal_plan, create_meal_plan, update_meal_plan):
    mcp.tool()(tool)

if __name__ == "__main__":
    mcp.run(transport="stdio")
