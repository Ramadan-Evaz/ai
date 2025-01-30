import requests

KALEMAT_BASE_URL = "https://api.kalimat.dev/search"
TOOL_NAME = "search_hadith"


class SearchHadith:
    def __init__(self, kalimat_api_key):
        self.api_key = kalimat_api_key
        self.base_url = KALEMAT_BASE_URL

    def get_tool_description(self):
        return {
            "type": "function",
            "function": {
                "name": "search_hadith",
                "description": "Search for relevant Hadith narrations based on a specific topic.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Topic or subject matter to search for in Hadith collections",
                        },
                    },
                    "required": ["query"],
                },
            },
        }

    def get_tool_name(self):
        return TOOL_NAME

    def run(self, query: str, num_results: int = 5):
        headers = {"x-api-key": self.api_key}
        payload = {
            "query": query,
            "numResults": num_results,
            "indexes": '["sunnah_lk"]',
            "getText": 2,
        }

        response = requests.get(self.base_url, headers=headers, params=payload)

        if response.status_code != 200:
            print(
                f"Query failed with code {response.status_code}, reason {response.reason}, text {response.text}",
            )
            response.raise_for_status()

        return response.json()

    def pp_hadith(self, h):
        en = h["en_text"]
        grade = h["grade_en"].strip()
        if grade:
            grade = f"\nGrade: {grade}\n"
        src = f"Collection: {h['source_book']} Chapter: {h['chapter_number']} Hadith: {h['hadith_number']} LK id: {h['id']}"
        result = f"{src}\n{en}\n{grade}"
        return result

    def run_as_list(self, query: str, num_results: int = 3):
        print(f'Searching hadith for "{query}"')
        results = self.run(query, num_results)
        return [self.pp_hadith(r) for r in results]

    def run_as_string(self, query: str, num_results: int = 3):
        results = self.run(query, num_results)
        rstring = "\n".join([self.pp_hadith(r) for r in results])
        return rstring
