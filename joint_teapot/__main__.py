__version__ = "0.0.0"

from datetime import datetime
from pathlib import Path
from typing import List

from typer import Argument, Typer, echo

from joint_teapot.teapot import Teapot
from joint_teapot.utils.logger import logger

app = Typer(add_completion=False)


class Tea:
    _teapot = None

    @property
    def pot(self) -> Teapot:
        if not self._teapot:
            self._teapot = Teapot()
        return self._teapot


tea = Tea()  # lazy loader


@app.command(
    "invite-to-teams", help="invite all canvas students to gitea teams by team name"
)
def add_all_canvas_students_to_teams(team_names: List[str]) -> None:
    tea.pot.add_all_canvas_students_to_teams(team_names)


@app.command(
    "create-personal-repos",
    help="create personal repos on gitea for all canvas students",
)
def create_personal_repos_for_all_canvas_students() -> None:
    tea.pot.create_personal_repos_for_all_canvas_students()


@app.command("create-teams", help="create teams on gitea by canvas groups")
def create_teams_and_repos_by_canvas_groups(group_prefix: str) -> None:
    tea.pot.create_teams_and_repos_by_canvas_groups(group_prefix)


@app.command("get-public-keys", help="list all public keys on gitea")
def get_public_key_of_all_canvas_students() -> None:
    echo("\n".join(tea.pot.get_public_key_of_all_canvas_students()))


@app.command("clone-all-repos", help="clone all gitea repos to local")
def clone_all_repos() -> None:
    tea.pot.clone_all_repos()


@app.command("create-issues", help="create issues on gitea")
def create_issue_for_repos(repo_names: List[str], title: str, body: str) -> None:
    tea.pot.create_issue_for_repos(repo_names, title, body)


@app.command("check-issues", help="check the existence of issue by title on gitea")
def check_exist_issue_by_title(repo_names: List[str], title: str) -> None:
    echo("\n".join(tea.pot.check_exist_issue_by_title(repo_names, title)))


@app.command(
    "checkout-releases",
    help="checkout git repo to git tag fetched from gitea by release name, with due date",
)
def checkout_to_repos_by_release_name(
    repo_names: List[str], release_name: str, due: datetime = Argument("3000-01-01")
) -> None:
    failed_repos = tea.pot.checkout_to_repos_by_release_name(
        repo_names, release_name, due
    )
    echo(f"failed repos: {failed_repos}")


@app.command(
    "close-all-issues", help="close all issues and pull requests in gitea organization"
)
def close_all_issues() -> None:
    tea.pot.gitea.close_all_issues()


@app.command("archieve-all-repos", help="archieve all repos in gitea organization")
def archieve_all_repos() -> None:
    tea.pot.gitea.archieve_all_repos()


@app.command("get-no-collaborator-repos", help="list all repos with no collaborators")
def get_no_collaborator_repos() -> None:
    tea.pot.gitea.get_no_collaborator_repos()


@app.command("get-repos-status", help="list status of all repos with conditions")
def get_repos_status(
    commit_lt: int = Argument(100000, help="commit count less than"),
    issue_lt: int = Argument(100000, help="issue count less than"),
) -> None:
    tea.pot.get_repos_status(commit_lt, issue_lt)


@app.command(
    "prepare-assignment-dir",
    help='prepare assignment dir from extracted canvas "Download Submissions" zip',
)
def prepare_assignment_dir(dir: Path) -> None:
    tea.pot.canvas.prepare_assignment_dir(str(dir))


@app.command(
    "upload-assignment-scores",
    help="upload assignment scores to canvas from score file (SCORE.txt by default), "
    + "read the first line as score, the rest as comments",
)
def upload_assignment_scores(dir: Path, assignment_name: str) -> None:
    tea.pot.canvas.upload_assignment_scores(str(dir), assignment_name)


if __name__ == "__main__":
    try:
        app()
    except Exception:
        logger.exception("Unexpected error:")
