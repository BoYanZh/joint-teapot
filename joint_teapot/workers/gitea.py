import json
import os
import re
import shutil
from datetime import datetime
from enum import Enum
from functools import lru_cache
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, TypeVar

import focs_gitea
import git
import pandas as pd
from canvasapi.group import Group, GroupMembership
from canvasapi.paginated_list import PaginatedList
from canvasapi.user import User
from focs_gitea.rest import ApiException

from joint_teapot.config import settings
from joint_teapot.utils.logger import logger
from joint_teapot.utils.main import default_repo_name_convertor, first


class PermissionEnum(Enum):
    read = "read"
    write = "write"
    admin = "admin"


T = TypeVar("T")


def list_all(method: Callable[..., Iterable[T]], *args: Any, **kwargs: Any) -> List[T]:
    all_res = []
    page = 1
    while True:
        res = method(*args, **kwargs, page=page)
        if not res:
            break
        for item in res:
            all_res.append(item)
        page += 1
    return all_res


class Gitea:
    def __init__(
        self,
        access_token: str = settings.gitea_access_token,
        org_name: str = settings.gitea_org_name,
        domain_name: str = settings.gitea_domain_name,
        suffix: str = settings.gitea_suffix,
    ):
        self.org_name = org_name
        configuration = focs_gitea.Configuration()
        configuration.api_key["access_token"] = access_token
        configuration.host = f"https://{domain_name}{suffix}/api/v1"
        configuration.debug = True
        for v in configuration.logger.values():
            v.handlers = []
        self.api_client = focs_gitea.ApiClient(configuration)
        self.admin_api = focs_gitea.AdminApi(self.api_client)
        self.miscellaneous_api = focs_gitea.MiscellaneousApi(self.api_client)
        self.organization_api = focs_gitea.OrganizationApi(self.api_client)
        self.issue_api = focs_gitea.IssueApi(self.api_client)
        self.repository_api = focs_gitea.RepositoryApi(self.api_client)
        self.settings_api = focs_gitea.SettingsApi(self.api_client)
        self.user_api = focs_gitea.UserApi(self.api_client)
        logger.debug("Gitea initialized")

    @lru_cache()
    def _get_team_id_by_name(self, name: str) -> int:
        res = self.organization_api.team_search(self.org_name, q=str(name), limit=1)
        if len(res["data"] or []) == 0:
            raise Exception(
                f"{name} not found by name in Gitea. Possible reason: you did not join this team."
            )
        return res["data"][0]["id"]

    @lru_cache()
    def _get_username_by_canvas_student(self, student: User) -> str:
        if student.integration_id is None:
            raise Exception(f"{student} id not found in Gitea")
        return student.integration_id

    def add_canvas_students_to_teams(
        self, students: PaginatedList, team_names: List[str]
    ) -> None:
        for team_name in team_names:
            team_id = self._get_team_id_by_name(team_name)
            team_members = self.organization_api.org_list_team_members(team_id)
            for student in students:
                try:
                    username = self._get_username_by_canvas_student(student)
                    team_member = first(team_members, lambda x: x.login == username)
                    if team_member is None:
                        self.organization_api.org_add_team_member(team_id, username)
                        logger.info(f"{student} added to team {team_name}")
                    else:
                        team_members.remove(team_member)
                        logger.warning(f"{student} already in team {team_name}")
                except Exception as e:
                    logger.error(e)
            for team_member in team_members:
                logger.error(
                    f"{team_member.full_name} found in team {team_name} "
                    + "but not found in Canvas students"
                )

    def create_personal_repos_for_canvas_students(
        self,
        students: PaginatedList,
        repo_name_convertor: Callable[
            [User], Optional[str]
        ] = default_repo_name_convertor,
    ) -> List[str]:
        repo_names = []
        for student in students:
            repo_name = repo_name_convertor(student)
            if repo_name is None:
                continue
            repo_names.append(repo_name)
            body = {
                "auto_init": False,
                "default_branch": "master",
                "name": repo_name,
                "private": True,
                "template": False,
                "trust_model": "default",
            }
            try:
                try:
                    self.organization_api.create_org_repo(self.org_name, body=body)
                    logger.info(
                        f"Personal repo {self.org_name}/{repo_name} for {student} created"
                    )
                except ApiException as e:
                    if e.status == 409:
                        logger.warning(
                            f"Personal repo {self.org_name}/{repo_name} for {student} already exists"
                        )
                    else:
                        raise (e)
                username = self._get_username_by_canvas_student(student)
                self.repository_api.repo_add_collaborator(
                    self.org_name, repo_name, username
                )
            except Exception as e:
                logger.error(e)
        return repo_names

    def create_teams_and_repos_by_canvas_groups(
        self,
        students: PaginatedList,
        groups: PaginatedList,
        team_name_convertor: Callable[[str], Optional[str]] = lambda name: name,
        repo_name_convertor: Callable[[str], Optional[str]] = lambda name: name,
        permission: PermissionEnum = PermissionEnum.write,
    ) -> List[str]:
        repo_names = []
        teams = list_all(self.organization_api.org_list_teams, self.org_name)
        repos = list_all(self.organization_api.org_list_repos, self.org_name)
        group: Group
        for group in groups:
            team_name = team_name_convertor(group.name)
            repo_name = repo_name_convertor(group.name)
            if team_name is None or repo_name is None:
                continue
            team = first(teams, lambda team: team.name == team_name)
            if team is None:
                team = self.organization_api.org_create_team(
                    self.org_name,
                    body={
                        "can_create_org_repo": False,
                        "includes_all_repositories": False,
                        "name": team_name,
                        "permission": permission.value,
                        "units": [
                            "repo.code",
                            "repo.issues",
                            "repo.ext_issues",
                            "repo.wiki",
                            "repo.pulls",
                            "repo.releases",
                            "repo.projects",
                            "repo.ext_wiki",
                        ],
                    },
                )
                logger.info(f"{self.org_name}/{team_name} created")
            if first(repos, lambda repo: repo.name == repo_name) is None:
                repo_names.append(repo_name)
                self.organization_api.create_org_repo(
                    self.org_name,
                    body={
                        "auto_init": False,
                        "default_branch": "master",
                        "name": repo_name,
                        "private": True,
                        "template": False,
                        "trust_model": "default",
                    },
                )
                logger.info(f"Team {team_name} created")
            self.organization_api.org_add_team_repository(
                team.id, self.org_name, repo_name
            )
            membership: GroupMembership
            student_count = 0
            for membership in group.get_memberships():
                student = first(students, lambda s: s.id == membership.user_id)
                student_count += 1
                if student is None:
                    raise Exception(
                        f"student with user_id {membership.user_id} not found"
                    )
                try:
                    username = self._get_username_by_canvas_student(student)
                except Exception as e:
                    logger.warning(e)
                    continue
                self.organization_api.org_add_team_member(team.id, username)
                self.repository_api.repo_add_collaborator(
                    self.org_name, repo_name, username
                )
            try:
                self.repository_api.repo_delete_branch_protection(
                    self.org_name, repo_name, "master"
                )
            except ApiException as e:
                if e.status != 404:
                    raise
            try:
                self.repository_api.repo_create_branch_protection(
                    self.org_name,
                    repo_name,
                    body={
                        "block_on_official_review_requests": True,
                        "block_on_outdated_branch": True,
                        "block_on_rejected_reviews": True,
                        "branch_name": "master",
                        "dismiss_stale_approvals": True,
                        "enable_approvals_whitelist": False,
                        "enable_merge_whitelist": False,
                        "enable_push": True,
                        "enable_push_whitelist": True,
                        "merge_whitelist_teams": [],
                        "merge_whitelist_usernames": [],
                        "protected_file_patterns": "",
                        "push_whitelist_deploy_keys": False,
                        "push_whitelist_teams": ["Owners"],
                        "push_whitelist_usernames": [],
                        "require_signed_commits": False,
                        "required_approvals": max(student_count - 1, 0),
                        "enable_status_check": True,
                        "status_check_contexts": ["continuous-integration/drone/pr"],
                    },
                )
            except ApiException as e:
                if e.status != 404:
                    raise
            logger.info(f"{self.org_name}/{repo_name} jobs done")
        return repo_names

    def get_public_key_of_canvas_students(
        self, students: PaginatedList
    ) -> Dict[str, List[str]]:
        res = {}
        for student in students:
            try:
                username = self._get_username_by_canvas_student(student)
                keys = [
                    item.key
                    for item in list_all(self.user_api.user_list_keys, username)
                ]
                if not keys:
                    logger.info(f"{student} has not uploaded ssh keys to gitea")
                    continue
                res[student.login_id] = keys
            except Exception as e:
                logger.error(e)
        return res

    def get_repo_releases(self, repo_name: str) -> List[Any]:
        try:
            args = self.repository_api.repo_list_releases, self.org_name, repo_name
            return list_all(*args)
        except ApiException as e:
            if e.status != 404:
                raise
        return []

    def get_all_repo_names(self) -> List[str]:
        return [
            data.name
            for data in list_all(self.organization_api.org_list_repos, self.org_name)
        ]

    def get_no_collaborator_repos(self) -> List[str]:
        res = []
        for data in list_all(self.organization_api.org_list_repos, self.org_name):
            collaborators = self.repository_api.repo_list_collaborators(
                self.org_name, data.name
            )
            if collaborators:
                continue
            logger.info(f"{self.org_name}/{data.name} has no collaborators")
            res.append(data.name)
        return res

    def get_repos_status(self) -> Dict[str, Tuple[int, int]]:
        res = {}
        for repo in list_all(self.organization_api.org_list_repos, self.org_name):
            commits = []
            issues = []
            try:
                commits = self.repository_api.repo_get_all_commits(
                    self.org_name, repo.name
                )
            except ApiException as e:
                if e.status != 409:
                    raise
            issues = self.issue_api.issue_list_issues(
                self.org_name, repo.name, state="all"
            )
            # if not commits:
            #     logger.info(f"{self.org_name}/{repo.name} has no commits")
            res[repo.name] = (len(commits), len(issues))
        return res

    def create_issue(
        self,
        repo_name: str,
        title: str,
        body: str,
        assign_every_collaborators: bool = True,
    ) -> None:
        assignees = []
        if assign_every_collaborators:
            assignees = [
                item.login
                for item in list_all(
                    self.repository_api.repo_list_collaborators,
                    self.org_name,
                    repo_name,
                )
            ]
        self.issue_api.issue_create_issue(
            self.org_name,
            repo_name,
            body={"title": title, "body": body, "assignees": assignees},
        )
        logger.info(f'Created issue "{title}" in {repo_name}')

    def create_milestone(
        self,
        repo_name: str,
        title: str,
        description: str,
        due_on: datetime,
    ) -> None:
        self.issue_api.issue_create_milestone(
            self.org_name,
            repo_name,
            body={
                "title": title,
                "description": description,
                "due_on": due_on.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            },
        )

    def check_exist_issue_by_title(self, repo_name: str, title: str) -> bool:
        for issue in list_all(
            self.issue_api.issue_list_issues, self.org_name, repo_name
        ):
            if issue.title == title:
                return True
        return False

    def close_all_issues(self) -> None:
        for repo_name in self.get_all_repo_names():
            issues = list_all(
                self.issue_api.issue_list_issues, self.org_name, repo_name
            )
            for issue in issues:
                if issue.state != "closed":
                    self.issue_api.issue_edit_issue(
                        self.org_name, repo_name, issue.number, body={"state": "closed"}
                    )

    def archive_all_repos(self) -> None:
        for repo in list_all(self.organization_api.org_list_repos, self.org_name):
            self.repository_api.repo_edit(
                self.org_name, repo.name, body={"archived": True}
            )

    def get_all_teams(self) -> Dict[str, List[str]]:
        res: Dict[str, List[str]] = {}
        for team in list_all(self.organization_api.org_list_teams, self.org_name):
            if team.name == "Owners":
                continue
            team_id = team.id
            try:
                members = [
                    m.login.lower()
                    for m in self.organization_api.org_list_team_members(team_id)
                ]
            except ApiException as e:
                logger.warning(
                    f"Failed to get members of team {team_id} in {self.org_name}: {e}"
                )
                continue
            res[team.name] = members
        return res

    def unsubscribe_from_repos(self, pattern: str) -> None:
        subscriptions = [
            sub
            for sub in self.user_api.user_current_list_subscriptions()
            if sub.owner.login == self.org_name
            and re.search(pattern, sub.name) is not None
        ]
        if len(subscriptions) == 0:
            logger.warning(f"No subscribed repo matches the pattern {pattern}")
            return
        logger.info(
            f"{len(subscriptions)} subscriptions match the pattern {pattern}: {[s.name for s in subscriptions]}"
        )
        for sub in subscriptions:
            self.repository_api.user_current_delete_subscription(
                self.org_name, sub.name
            )
            logger.info(f"Unsubscribed from {sub.name}")

    def JOJ3_scoreboard(
        self,
        scorefile_path: str,
        repo_path: str,
        scoreboard_file_name: str,
        remote_repo: str,
    ) -> None:
        if not scorefile_path.endswith(".json"):
            logger.error(
                f"Score file should be a .json file, but now it is {scorefile_path}"
            )
            return
        if not scoreboard_file_name.endswith(".csv"):
            logger.error(
                f"Scoreboard file should be a .csv file, but now it is {scoreboard_file_name}"
            )
            return
        if remote_repo != "" and os.path.exists(repo_path):
            logger.error(
                f"Local file or folder {repo_path} already exists, you are not allowed to clone there. Please first remove that manually!"
            )
            return

        # Init gitea repo
        if remote_repo == "":
            repo = git.Repo(repo_path)
            if repo.bare:
                logger.error(f"{repo_path} is not a valid git repo!")
                return
        else:
            repo = git.Repo.clone_from(remote_repo, repo_path, branch="grading")
        origin = repo.remote(name="origin")
        origin.pull()

        # Switch to grading branch
        if "grading" in repo.branches:
            repo.git.checkout("grading")
        else:
            logger.error('Please first create a "grading" branch in that repo!')
            return

        # Load the csv file if it already exists
        if os.path.exists(os.path.join(repo_path, scoreboard_file_name)):
            df = pd.read_csv(os.path.join(repo_path, scoreboard_file_name))
            columns = ["" if x == "Unnamed: 0" else x for x in df.columns]
            df.columns = columns
            for col in df.columns[2:]:
                df[col] = df[col].astype("Int64")
        else:
            data: Dict[str, List[Any]] = {
                "": [],
                "last_edit": [],  # This is just to make changes in the file so that it can be pushed.
                # Only used in development stage. Will be removed in the future.
                "total": [],
            }
            df = pd.DataFrame(data)

        # Update data
        with open(scorefile_path) as json_file:
            scoreboard: Dict[str, Any] = json.load(json_file)

        student = f"{scoreboard['studentname']} {scoreboard['studentid']}"
        if not (df.iloc[:, 0].isin([student]).any()):
            newrow = [student, 0, ""] + [None] * (
                len(df.columns) - 3
            )  # In formal version should be -2
            df.loc[len(df)] = newrow

        for stagerecord in scoreboard["stagerecords"]:
            stagename = stagerecord["stagename"]
            for stageresult in stagerecord["stageresults"]:
                name = stageresult["name"]
                for i, result in enumerate(stageresult["results"]):
                    score = result["score"]
                    colname = f"{stagename}/{name}"
                    if len(stageresult["results"]) != 1:
                        colname = f"{colname}/{i}"
                    if colname not in df.columns:
                        df[colname] = pd.Series([None] * len(df), dtype="Int64")
                    df.loc[df.iloc[:, 0] == student, colname] = score

        total = 0
        for col in df.columns:
            if (col in ["", "total", "last_edit"]) or (
                df.loc[df.iloc[:, 0] == student, col].isna().values[0]
            ):
                continue
            total += df.loc[df.iloc[:, 0] == student, col]

        df.loc[df.iloc[:, 0] == student, "total"] = total

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        df.loc[
            df.iloc[:, 0] == student, "last_edit"
        ] = now  # Delete this in formal version

        # Write back to the csv file:
        df = df.sort_values(by="total", ascending=False)
        df.to_csv(os.path.join(repo_path, scoreboard_file_name), index=False)

        # Push to gitea
        repo.index.add([scoreboard_file_name])
        repo.index.commit(f"test: JOJ3-dev testing at {now}")
        origin.push()

        # Remove local repo
        if remote_repo != "":
            shutil.rmtree(repo_path)


if __name__ == "__main__":
    gitea = Gitea()
