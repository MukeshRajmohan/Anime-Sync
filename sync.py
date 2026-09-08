import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from notion_client import Client
from playwright.sync_api import sync_playwright

load_dotenv()
notion = Client(auth=os.environ.get("NOTION_TOKEN"))

def fetch_anilist_data(page, al_id):
    query = '''
    query ($id: Int) {
      Media (id: $id, type: ANIME) {
        episodes
        averageScore
        status
        season
        seasonYear
        genres
        coverImage {
          extraLarge
        }
        nextAiringEpisode {
          airingAt
          episode
        }
      }
    }
    '''
    
    js_code = f"""
    async () => {{
        try {{
            const response = await fetch('https://graphql.anilist.co', {{
                method: 'POST',
                headers: {{
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                }},
                body: JSON.stringify({{
                    query: `{query}`,
                    variables: {{ id: {al_id} }}
                }})
            }});
            
            if (response.ok) {{
                const json = await response.json();
                return {{ success: true, data: json }};
            }} else {{
                return {{ success: false, status: response.status }};
            }}
        }} catch (e) {{
            return {{ success: false, error: e.toString() }};
        }}
    }}
    """
    
    try:
        result = page.evaluate(js_code)
        
        if not result.get("success"):
            print(f"⚠️ In-browser fetch failed for AL ID {al_id}: {result}")
            return None
            
        data = result.get("data", {}).get("data", {}).get("Media")
        if not data:
            return None
            
        # Map AniList statuses to Notion terminology
        status_map = {
            "FINISHED": "Finished Airing",
            "RELEASING": "Currently Airing",
            "NOT_YET_RELEASED": "Not Yet Aired",
            "CANCELLED": "Cancelled",
            "HIATUS": "On Hiatus"
        }
        status = status_map.get(data.get("status"), data.get("status"))
        
        # Combine Season and Year (e.g., "Winter 2024")
        season_str = None
        if data.get("season") and data.get("seasonYear"):
            season_str = f"{data['season'].capitalize()} {data['seasonYear']}"
            
        # Convert next airing Epoch timestamp to ISO 8601 (EST/EDT)
        next_airing_iso = None
        next_episode = None
        if data.get("nextAiringEpisode"):
            if data["nextAiringEpisode"].get("airingAt"):
                airing_at = data["nextAiringEpisode"]["airingAt"]
                next_airing_iso = datetime.fromtimestamp(airing_at, ZoneInfo("America/New_York")).isoformat()
            
            # Extract the episode number
            if data["nextAiringEpisode"].get("episode"):
                next_episode = data["nextAiringEpisode"]["episode"]
            
        # Convert AniList 100-point scale to 10-point scale
        score = data.get("averageScore") / 10.0 if data.get("averageScore") else None
            
        return {
            "poster_url": data.get("coverImage", {}).get("extraLarge"),
            "episodes": data.get("episodes"),
            "score": score,
            "status": status,
            "season": season_str,
            "genres": data.get("genres", []),
            "next_airing": next_airing_iso,
            "next_episode": next_episode
        }
    except Exception as e:
        print(f"⚠️ Execution error for AL ID {al_id}: {e}")
        return None

def main():
    database_id = os.environ.get("NOTION_DATABASE_ID")
    has_more = True
    next_cursor = None
    
    print("Starting AniList synchronization via Playwright page session...")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )
        
        print("Establishing trusted session with AniList...")
        page = context.new_page()
        page.goto("https://anilist.co", wait_until="domcontentloaded")
        time.sleep(5)
        
        while has_more:
            results = notion.data_sources.query(
                data_source_id=database_id,
                filter={
                    "property": "AL ID",
                    "number": {
                        "is_not_empty": True
                    }
                },
                start_cursor=next_cursor
            )

            '''results = notion.data_sources.query(
                data_source_id=database_id,
                filter={
                    "and": [
                        {
                            "property": "AL ID",
                            "number": {
                                "is_not_empty": True
                            }
                        },
                        {
                            "or": [
                                {
                                    "property": "Status",
                                    "select": {
                                        "equals": "Watching" 
                                    }
                                },
                                {
                                    "property": "Status",
                                    "select": {
                                        "equals": "Planning" 
                                    }
                                }
                            ]
                        }
                    ]
                },
                start_cursor=next_cursor
            )'''
            
            for row in results["results"]:
                al_id_prop = row["properties"].get("AL ID")
                if not al_id_prop or al_id_prop.get("type") != "number" or not al_id_prop["number"]:
                    continue
                    
                al_id = al_id_prop["number"]
                print(f"Fetching data for AL ID: {al_id}")
                data = fetch_anilist_data(page, al_id)
                
                if data:
                    now_iso = datetime.now(ZoneInfo("America/New_York")).isoformat()
                    
                    props = {
                        "Last Sync": {"date": {"start": now_iso}},
                        "Sync State": {"status": {"name": "Synced"}} 
                    }
                    
                    if data["episodes"] is not None:
                        props["Total Episodes"] = {"number": data["episodes"]}
                    if data["score"] is not None:
                        props["AL Score"] = {"number": data["score"]} 
                    if data["poster_url"]:
                        props["Poster"] = {"files": [{"type": "external", "name": "Poster", "external": {"url": data["poster_url"]}}]}
                    if data["status"]:
                        props["Airing Status"] = {"select": {"name": data["status"]}}
                    if data["season"]:
                        props["Season"] = {"rich_text": [{"text": {"content": data["season"]}}]}
                    if data["genres"]:
                        props["Genre"] = {"multi_select": [{"name": g} for g in data["genres"]]}
                    if data["next_airing"]:
                        props["Airing"] = {"date": {"start": data["next_airing"]}}
                    if data.get("next_episode"):
                        props["Next Episode"] = {"number": data["next_episode"]}

                    try:
                        notion.pages.update(
                            page_id=row["id"],
                            properties=props
                        )
                        print(f"✅ Updated Notion for AL ID: {al_id}")
                    except Exception as e:
                        print(f"❌ Update failed for {al_id}: {e}")
                
                # Respect AniList's 90 req/min rate limit
                time.sleep(1.5)  
                
            has_more = results["has_more"]
            next_cursor = results["next_cursor"]
            
        browser.close()
    print("Sync complete!")

if __name__ == "__main__":
    main()