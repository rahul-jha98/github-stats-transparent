#!/usr/bin/python3
"""Fetch GitHub user stats via GraphQL and REST APIs."""

import asyncio
import os
import random
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import quote

import aiohttp
import requests

###############################################################################
# Main Classes
###############################################################################


class Queries(object):
    """
    Class with functions to query the GitHub GraphQL (v4) API and the REST (v3)
    API. Also includes functions to dynamically generate GraphQL queries.
    """

    def __init__(
        self,
        username: Optional[str],
        access_token: str,
        session: aiohttp.ClientSession,
        max_connections: int = 10,
    ):
        self.username = username
        self.access_token = access_token
        self.session = session
        self.semaphore = asyncio.Semaphore(max_connections)

    async def query(self, generated_query: str) -> Dict:
        """
        Make a request to the GraphQL API using the authentication token from
        the environment
        :param generated_query: string query to be sent to the API
        :return: decoded GraphQL JSON output
        """
        headers = {
            "Authorization": f"Bearer {self.access_token}",
        }
        try:
            async with self.semaphore:
                r = await self.session.post(
                    "https://api.github.com/graphql",
                    headers=headers,
                    json={"query": generated_query},
                )
            return await r.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            print(f"aiohttp failed for GraphQL query: {exc}")
            # Fall back on non-async requests
            async with self.semaphore:
                r = requests.post(
                    "https://api.github.com/graphql",
                    headers=headers,
                    json={"query": generated_query},
                    timeout=30,
                )
                return r.json()

    async def query_rest(self, path: str, params: Optional[Dict] = None) -> Dict:
        """
        Make a request to the REST API
        :param path: API path to query
        :param params: Query parameters to be passed to the API
        :return: deserialized REST JSON output
        """
        _, result = await self.query_rest_response(path, params=params)
        return dict() if result is None else result

    async def query_rest_response(
        self,
        path: str,
        params: Optional[Dict] = None,
        max_attempts: int = 10,
        retry_statuses: Optional[Set[int]] = None,
    ) -> Tuple[int, object]:
        """
        Make a request to the REST API and return both the status and body.
        :param path: API path to query
        :param params: Query parameters to be passed to the API
        :param max_attempts: maximum number of attempts before giving up
        :param retry_statuses: HTTP statuses that should be retried
        :return: (status code, deserialized REST JSON output)
        """
        if params is None:
            params = dict()
        if path.startswith("/"):
            path = path[1:]
        if retry_statuses is None:
            retry_statuses = {202}
        headers = {
            "Authorization": f"token {self.access_token}",
        }

        last_status = 0
        last_result: object = dict()
        for attempt in range(max_attempts):
            try:
                async with self.semaphore:
                    r = await self.session.get(
                        f"https://api.github.com/{path}",
                        headers=headers,
                        params=tuple(params.items()),
                    )
                last_status = r.status
                if r.status == 204:
                    return r.status, dict()

                try:
                    last_result = await r.json()
                except ValueError:
                    last_result = dict()

                if r.status in retry_statuses and attempt + 1 < max_attempts:
                    delay = random.uniform(0, 4)
                    print(
                        f"{path} returned {r.status}. Retrying in {delay:.1f}s"
                        f" (attempt {attempt + 1}/{max_attempts})..."
                    )
                    await asyncio.sleep(delay)
                    continue
                return last_status, last_result
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                print(f"aiohttp failed for rest query: {exc}")
                # Fall back on non-async requests
                async with self.semaphore:
                    try:
                        r = requests.get(
                            f"https://api.github.com/{path}",
                            headers=headers,
                            params=tuple(params.items()),
                            timeout=30,
                        )
                    except Exception as sync_exc:
                        print(f"requests failed for rest query: {sync_exc}")
                        continue

                    last_status = r.status_code
                    if r.status_code == 204:
                        return r.status_code, dict()

                    try:
                        last_result = r.json()
                    except ValueError:
                        last_result = dict()

                    if r.status_code in retry_statuses and attempt + 1 < max_attempts:
                        delay = random.uniform(0, 4)
                        print(
                            f"{path} returned {r.status_code}. Retrying in {delay:.1f}s"
                            f" (attempt {attempt + 1}/{max_attempts})..."
                        )
                        await asyncio.sleep(delay)
                        continue
                    return last_status, last_result

        if last_status in retry_statuses:
            print(
                f"Too many {last_status}s for {path}. Falling back to git clone if applicable."
            )
        return last_status, last_result

    @staticmethod
    def repos_overview(
        contrib_cursor: Optional[str] = None, owned_cursor: Optional[str] = None
    ) -> str:
        """
        :return: GraphQL query with overview of user repositories
        """
        return f"""{{
  viewer {{
    login,
    name,
    repositories(
        first: 100,
        orderBy: {{
            field: UPDATED_AT,
            direction: DESC
        }},
        isFork: false,
        after: {"null" if owned_cursor is None else '"'+ owned_cursor +'"'}
    ) {{
      pageInfo {{
        hasNextPage
        endCursor
      }}
      nodes {{
        nameWithOwner
        stargazers {{
          totalCount
        }}
        forkCount
        languages(first: 10, orderBy: {{field: SIZE, direction: DESC}}) {{
          edges {{
            size
            node {{
              name
              color
            }}
          }}
        }}
      }}
    }}
    repositoriesContributedTo(
        first: 100,
        includeUserRepositories: false,
        orderBy: {{
            field: UPDATED_AT,
            direction: DESC
        }},
        contributionTypes: [
            COMMIT,
            PULL_REQUEST,
            REPOSITORY,
            PULL_REQUEST_REVIEW
        ]
        after: {"null" if contrib_cursor is None else '"'+ contrib_cursor +'"'}
    ) {{
      pageInfo {{
        hasNextPage
        endCursor
      }}
      nodes {{
        nameWithOwner
        stargazers {{
          totalCount
        }}
        forkCount
        languages(first: 10, orderBy: {{field: SIZE, direction: DESC}}) {{
          edges {{
            size
            node {{
              name
              color
            }}
          }}
        }}
      }}
    }}
  }}
}}
"""

    @staticmethod
    def contrib_years() -> str:
        """
        :return: GraphQL query to get all years the user has been a contributor
        """
        return """
query {
  viewer {
    contributionsCollection {
      contributionYears
    }
  }
}
"""

    @staticmethod
    def contribs_by_year(year: str) -> str:
        """
        :param year: year to query for
        :return: portion of a GraphQL query with desired info for a given year
        """
        return f"""
    year{year}: contributionsCollection(
        from: "{year}-01-01T00:00:00Z",
        to: "{int(year) + 1}-01-01T00:00:00Z"
    ) {{
      contributionCalendar {{
        totalContributions
      }}
    }}
"""

    @classmethod
    def all_contribs(cls, years: List[str]) -> str:
        """
        :param years: list of years to get contributions for
        :return: query to retrieve contribution information for all user years
        """
        by_years = "\n".join(map(cls.contribs_by_year, years))
        return f"""
query {{
  viewer {{
    {by_years}
  }}
}}
"""


class Stats(object):
    """
    Retrieve and store statistics about GitHub usage.
    """

    def __init__(
        self,
        username: Optional[str],
        access_token: str,
        session: aiohttp.ClientSession,
        exclude_repos: Optional[Set] = None,
        exclude_langs: Optional[Set] = None,
        consider_forked_repos: bool = False,
    ):
        self.username = username
        self._exclude_repos = set() if exclude_repos is None else exclude_repos
        self._exclude_langs = set() if exclude_langs is None else exclude_langs
        self._consider_forked_repos = consider_forked_repos
        self.queries = Queries(username, access_token, session)
        self._ignored_repos = set()

        self._clone_semaphore = asyncio.Semaphore(3)
        self._user_emails_cache: Optional[List[str]] = None

        self._login = username
        self._name = None
        self._stargazers = None
        self._forks = None
        self._total_contributions = None
        self._languages = None
        self._repos = None
        self._lines_changed = None
        self._views = None

    async def to_str(self) -> str:
        """
        :return: summary of all available statistics
        """
        languages = await self.languages_proportional
        formatted_languages = "\n  - ".join(
            [f"{k}: {v:0.4f}%" for k, v in languages.items()]
        )
        lines_changed = await self.lines_changed
        return f"""Name: {await self.name}
Stargazers: {await self.stargazers:,}
Forks: {await self.forks:,}
All-time contributions: {await self.total_contributions:,}
Repositories with contributions: {len(await self.all_repos)}
Lines of code added: {lines_changed[0]:,}
Lines of code deleted: {lines_changed[1]:,}
Lines of code changed: {lines_changed[0] + lines_changed[1]:,}
Project page views: {await self.views:,}
Languages:
  - {formatted_languages}"""

    async def get_stats(self) -> None:
        """
        Get lots of summary statistics using one big query. Sets many attributes
        """
        self._stargazers = 0
        self._forks = 0
        self._languages = dict()
        self._repos = set()
        self._ignored_repos = set()

        next_owned = None
        next_contrib = None
        while True:
            raw_results = await self.queries.query(
                Queries.repos_overview(
                    owned_cursor=next_owned, contrib_cursor=next_contrib
                )
            )
            raw_results = raw_results if raw_results is not None else {}
            viewer = raw_results.get("data", {}).get("viewer", {})

            self._login = viewer.get("login", self._login)
            self._name = viewer.get("name", None)
            if self._name is None:
                self._name = viewer.get("login", "No Name")

            contrib_repos = viewer.get("repositoriesContributedTo", {})
            owned_repos = viewer.get("repositories", {})

            repos = owned_repos.get("nodes", [])
            if self._consider_forked_repos:
                repos += contrib_repos.get("nodes", [])
            else:
                for repo in contrib_repos.get("nodes", []):
                    name = repo.get("nameWithOwner")
                    if name in self._ignored_repos or name in self._exclude_repos:
                        continue
                    self._ignored_repos.add(name)

            for repo in repos:
                name = repo.get("nameWithOwner")
                if name in self._repos or name in self._exclude_repos:
                    continue
                self._repos.add(name)
                self._stargazers += repo.get("stargazers").get("totalCount", 0)
                self._forks += repo.get("forkCount", 0)

                for lang in repo.get("languages", {}).get("edges", []):
                    name = lang.get("node", {}).get("name", "Other")
                    languages = await self.languages
                    if name in self._exclude_langs:
                        continue
                    if name in languages:
                        languages[name]["size"] += lang.get("size", 0)
                        languages[name]["occurrences"] += 1
                    else:
                        languages[name] = {
                            "size": lang.get("size", 0),
                            "occurrences": 1,
                            "color": lang.get("node", {}).get("color"),
                        }

            if owned_repos.get("pageInfo", {}).get(
                "hasNextPage", False
            ) or contrib_repos.get("pageInfo", {}).get("hasNextPage", False):
                next_owned = owned_repos.get("pageInfo", {}).get(
                    "endCursor", next_owned
                )
                next_contrib = contrib_repos.get("pageInfo", {}).get(
                    "endCursor", next_contrib
                )
            else:
                break

        langs_total = 0
        for v in self._languages.values():
            weight = v.get("occurrences", 1)
            weighted_size = v.get("size", 0) * weight
            v["weighted_size"] = weighted_size
            langs_total += weighted_size

        if langs_total > 0:
            for v in self._languages.values():
                v["prop"] = 100 * (v.get("weighted_size", 0) / langs_total)
        else:
            for v in self._languages.values():
                v["prop"] = 0

    @property
    async def name(self) -> str:
        """
        :return: GitHub user's name (e.g., Dongmin, Yu)
        """
        if self._name is not None:
            return self._name
        await self.get_stats()
        assert self._name is not None
        return self._name

    @property
    async def stargazers(self) -> int:
        """
        :return: total number of stargazers on user's repos
        """
        if self._stargazers is not None:
            return self._stargazers
        await self.get_stats()
        assert self._stargazers is not None
        return self._stargazers

    @property
    async def forks(self) -> int:
        """
        :return: total number of forks on user's repos
        """
        if self._forks is not None:
            return self._forks
        await self.get_stats()
        assert self._forks is not None
        return self._forks

    @property
    async def languages(self) -> Dict:
        """
        :return: summary of languages used by the user
        """
        if self._languages is not None:
            return self._languages
        await self.get_stats()
        assert self._languages is not None
        return self._languages

    @property
    async def languages_proportional(self) -> Dict:
        """
        :return: summary of languages used by the user, with proportional usage
        """
        if self._languages is None:
            await self.get_stats()
            assert self._languages is not None

        return {k: v.get("prop", 0) for (k, v) in self._languages.items()}

    @property
    async def repos(self) -> List[str]:
        """
        :return: list of names of user's repos
        """
        if self._repos is not None:
            return self._repos
        await self.get_stats()
        assert self._repos is not None
        return self._repos

    @property
    async def all_repos(self) -> List[str]:
        """
        :return: list of names of user's repos with contributed repos included
                irrespective of whether the ignore flag is set or not
        """
        if self._repos is not None and self._ignored_repos is not None:
            return self._repos | self._ignored_repos
        await self.get_stats()
        assert self._repos is not None
        assert self._ignored_repos is not None
        return self._repos | self._ignored_repos

    @property
    async def total_contributions(self) -> int:
        """
        :return: count of user's total contributions as defined by GitHub
        """
        if self._total_contributions is not None:
            return self._total_contributions

        self._total_contributions = 0
        years = (
            (await self.queries.query(Queries.contrib_years()))
            .get("data", {})
            .get("viewer", {})
            .get("contributionsCollection", {})
            .get("contributionYears", [])
        )
        by_year = (
            (await self.queries.query(Queries.all_contribs(years)))
            .get("data", {})
            .get("viewer", {})
            .values()
        )
        for year in by_year:
            self._total_contributions += year.get("contributionCalendar", {}).get(
                "totalContributions", 0
            )
        return self._total_contributions

    @property
    async def lines_changed(self) -> Tuple[int, int]:
        """
        :return: count of total lines added, removed, or modified by the user
        """
        if self._lines_changed is not None:
            return self._lines_changed

        # External contributed-to repos (all_repos) cause persistent 202s:
        # their large commit histories overwhelm GitHub's stats computation.
        repos = list(await self.repos)

        results = await asyncio.gather(*[self._fetch_lines_changed(repo) for repo in repos])
        total_additions = sum(r[0] for r in results)
        total_deletions = sum(r[1] for r in results)
        self._lines_changed = (total_additions, total_deletions)
        return self._lines_changed

    async def _get_login(self) -> str:
        if self._login is not None:
            return self._login
        await self.get_stats()
        if self._login is not None:
            return self._login
        return "x-access-token"

    async def _fetch_lines_changed(self, repo: str) -> Tuple[int, int]:
        status, response = await self.queries.query_rest_response(
            f"/repos/{repo}/stats/contributors",
            max_attempts=10,
            retry_statuses={202, 403, 429},
        )

        if status == 200 and isinstance(response, list):
            additions = 0
            deletions = 0
            login = await self._get_login()
            for author_obj in response:
                if not isinstance(author_obj, dict) or not isinstance(
                    author_obj.get("author", {}), dict
                ):
                    continue
                author = author_obj.get("author", {}).get("login", "")
                if author != login:
                    continue
                for week in author_obj.get("weeks", []):
                    additions += week.get("a", 0)
                    deletions += week.get("d", 0)
            return additions, deletions

        if status in {202, 403, 429}:
            return await self._get_lines_changed_from_git(repo)

        if status not in {0, 204}:
            print(f"Failed to get contribution data for {repo} (status: {status}).")
        return 0, 0

    async def _get_user_emails(self) -> List[str]:
        if self._user_emails_cache is not None:
            return self._user_emails_cache

        status, response = await self.queries.query_rest_response(
            "/user/emails",
            max_attempts=1,
        )
        emails = []
        if status == 200 and isinstance(response, list):
            emails = [
                email_obj.get("email")
                for email_obj in response
                if isinstance(email_obj, dict) and email_obj.get("email")
            ]

        if not emails:
            login = await self._get_login()
            print(
                "Failed to get user emails. Falling back to the noreply address; "
                "token may be missing user:email permission."
            )
            emails = [f"{login}@users.noreply.github.com"]

        self._user_emails_cache = emails
        return self._user_emails_cache

    async def _get_lines_changed_from_git(self, repo: str) -> Tuple[int, int]:
        if shutil.which("git") is None:
            print("git is not installed. Skipping git fallback for lines changed.")
            return 0, 0

        login = await self._get_login()
        emails = await self._get_user_emails()
        print(f"Cloning {repo} to get lines changed...")
        async with self._clone_semaphore:
            additions, deletions = await asyncio.to_thread(
                self._get_lines_changed_from_git_sync,
                repo,
                emails,
                login,
            )
        print(
            f"Got {additions + deletions} line(s) changed by {login} in {repo}"
        )
        return additions, deletions

    def _get_lines_changed_from_git_sync(
        self, repo: str, emails: List[str], login: str
    ) -> Tuple[int, int]:
        repo_name = repo.replace("/", "_")
        safe_username = quote(login, safe="")
        safe_token = quote(self.queries.access_token, safe="")
        repo_url = f"https://{safe_username}:{safe_token}@github.com/{repo}.git"

        with tempfile.TemporaryDirectory(prefix="github-stats-") as temp_dir:
            repo_path = os.path.join(temp_dir, repo_name)
            clone = subprocess.run(
                [
                    "git",
                    "clone",
                    "--bare",
                    "--filter=blob:limit=1m",
                    "--no-tags",
                    "--single-branch",
                    repo_url,
                    repo_path,
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if clone.returncode != 0:
                print(f"Failed to clone {repo} to compute lines changed.")
                return 0, 0

            log_command = ["git", "-C", repo_path, "log", "--numstat", "--pretty=tformat:"]
            for email in emails:
                log_command.extend(["--author", email])

            log = subprocess.run(
                log_command,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if log.returncode != 0:
                print(f"Failed to inspect git history for {repo}.")
                return 0, 0

        additions = 0
        deletions = 0
        for line in log.stdout.splitlines():
            if not line:
                continue
            parts = line.split("\t", 2)
            if len(parts) < 2:
                continue
            try:
                additions += int(parts[0])
            except ValueError:
                pass
            try:
                deletions += int(parts[1])
            except ValueError:
                pass

        return additions, deletions

    @property
    async def views(self) -> int:
        """
        Note: only returns views for the last 14 days (as-per GitHub API)
        :return: total number of page views the user's projects have received
        """
        if self._views is not None:
            return self._views

        async def fetch_views(repo: str) -> int:
            r = await self.queries.query_rest(f"/repos/{repo}/traffic/views")
            return sum(view.get("count", 0) for view in r.get("views", []))

        results = await asyncio.gather(
            *[fetch_views(repo) for repo in await self.repos]
        )
        self._views = sum(results)
        return self._views


###############################################################################
# Main Function
###############################################################################


async def main() -> None:
    """
    Used mostly for testing; this module is not usually run standalone
    """
    access_token = os.getenv("ACCESS_TOKEN")
    user = os.getenv("GITHUB_ACTOR")
    async with aiohttp.ClientSession() as session:
        s = Stats(user, access_token, session)
        print(await s.to_str())


if __name__ == "__main__":
    asyncio.run(main())
