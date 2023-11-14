from github import Auth, Github
import traceback
import re


class GithubHandler:
    """
    Handles interactions with the GitHub API.
    """

    def __init__(
        self, repo_name: str, main_branch: str = "main", auth_token: str = None
    ):
        """
        Initializes a new instance of the GithubHandler class.

        Args:
            repo_name (str): The name of the repository to interact with.
            main_branch (str): The name of the main branch of the repository. Defaults to "main".
            auth_token (str): The authentication token to use for interacting with the repository. Defaults to None.
        """
        self.repo_name = repo_name
        self.authenticate(auth_token=auth_token)
        self.repo = self.g.get_repo(self.repo_name)
        self.prs = self.repo.get_pulls(state="open", sort="created", base=main_branch)
        self.prs_nums = [pr.number for pr in self.prs]
        self.prs_dict = {k: v for k, v in zip(self.prs_nums, self.prs)}

    def get_commits(self):
        """
        Gets the commits for each open pull request in the repository.
        """
        for pr_num in self.prs_dict:
            pr = self.prs_dict[pr_num]["pr"]
            commits = pr.get_commits()
            commit_list = []

            for commit in commits:
                commit_obj = self.repo.get_commit(commit.sha)
                commit_data = {"sha": commit.sha, "files": []}

                for file in commit_obj.files:
                    file_data = {
                        "filename": file.filename,
                        "status": file.status,
                        "patch": file.patch,
                    }
                    commit_data["files"].append(file_data)

                commit_list.append(commit_data)

            self.prs_dict[pr_num]["commits"] = commit_list

    def authenticate(self, auth_token=None):
        """
        Authenticates with the GitHub API.
        """

        class AuthException(Exception):
            pass

        if not auth_token:
            raise AuthException("No github authentication token provided.")

        self.auth = Auth.Token(auth_token)
        self.g = Github(auth=self.auth)

    def get_latest_pr_from_branch(self, branch: str, downstream_branch: str = None):
        """
        Gets the latest pull request from a given branch.

        Args:
            branch (str): The name of the branch to search for pull requests.
            downstream_branch (str, optional): The name of the downstream branch to filter by. Defaults to None.

        Returns:
            PullRequest: The latest pull request from the specified branch.
        """
        prs = self.repo.get_pulls(
            state="open", sort="created", base=branch, direction="desc"
        )
        if downstream_branch:
            for pr in prs:
                if pr.head.ref == downstream_branch:
                    return pr
        return prs[0]

    def get_pr_from_id(self, pr_id: int):
        """
        Gets a pull request by its ID.

        Args:
            pr_id (int): The ID of the pull request to retrieve.

        Returns:
            PullRequest: The pull request with the specified ID.
        """
        pr = self.repo.get_pull(pr_id)
        return pr

    def get_all_files(self):
        """
        Gets all files in the repository.
        """
        contents = self.repo.get_contents("")

        while contents:
            file_content = contents.pop(0)
            if file_content.type == "dir":
                contents.extend(self.repo.get_contents(file_content.path))

        self.all_files = contents

    def get_file_contents(self, filepath: str, ref=None):
        """
        Gets the contents of a file in the repository.

        Args:
            filepath (str): The path to the file.
            ref (str, optional): The reference to the commit. Defaults to None.

        Returns:
            str: The contents of the file.
        """
        try:
            file_contents = self.repo.get_contents(filepath, ref=ref)
            return file_contents.decoded_content.decode()
        except:
            traceback.print_exc()
            return "Could not retrieve file contents."

    def get_pr_deltas(self, pr):
        """
        Gets the deltas for a pull request.

        Args:
            pr (PullRequest): The pull request to get deltas for.

        Returns:
            str: The deltas for the pull request.
        """
        files_and_deltas = ""
        file_changes = pr.get_files()
        for f in file_changes:
            file_full_content = self.get_file_contents(f.filename, ref=pr.head.sha)
            full_file_delta = self.get_full_file_delta(f.filename, file_full_content)
            file_delta = self.get_file_delta(f.patch, f.filename)
            files_and_deltas += full_file_delta + "\n" + file_delta + "\n"

        return files_and_deltas

    def get_full_file_delta(self, filename, file_full_content):
        """
        Gets the full file content for a file in the repository.

        Args:
            filename (str): The path to the file.
            file_full_content (str): The full content of the file.

        Returns:
            str: The full file content formatted for position reviews.
        """
        return (
            f"{filename} - (full file before changes - this is just for context, DONT REVIEW THIS CODE, ITs Not part of the PR)\n"
            + "-" * 30
            + "\n"
            + file_full_content
            + "\n"
            + "-" * 30
            + "\n"
        )

    def get_file_delta(self, patch, filename):
        """
        Gets the deltas for a file in the repository.

        Args:
            patch (str): The patch for the file.
            filename (str): The path to the file.

        Returns:
            str: The deltas for the file formatted for position reviews.
        """
        lines = patch.split("\n")
        deltas = ""

        deltas += f"{filename} - (deltas - This is the code you need to review)\n"
        deltas += "-" * 30 + "\n"

        for line in lines:
            if line.startswith("@@"):
                deltas += self.get_chunk_header(line)

                add_start_add_len = list(
                    map(int, re.findall("\+(.+?) ", line)[0].split(","))
                )
                rem_start_rem_len = list(
                    map(int, re.findall("\-(.+?) ", line)[0].split(","))
                )

                if len(add_start_add_len) == 1:
                    return f"{filename}\n" + "-" * 30 + "\n"

                add_start, add_len = add_start_add_len
                rem_start, rem_len = rem_start_rem_len

                current_add_line = add_start
                current_rem_line = rem_start
            else:
                if line.startswith("-"):
                    deltas += self.get_line_delta(line, current_rem_line, "LEFT")
                    current_rem_line += 1
                else:
                    deltas += self.get_line_delta(line, current_add_line, "RIGHT")
                    current_add_line += 1

        return f"{filename}\n{deltas}\n" + "-" * 30 + "\n"

    def get_chunk_header(self, line):
        """
        Gets the header for a chunk of code.

        Args:
            line (str): The line containing the chunk header.

        Returns:
            str: The chunk header formatted for position reviews.
        """
        return "-" * 30 + "\n" + f"{line}\n"

    def get_line_delta(self, line, line_number, side):
        """
        Gets the delta for a line of code.

        Args:
            line (str): The line of code.
            line_number (int): The line number.
            side (str): The side of the code (LEFT or RIGHT).

        Returns:
            str: The delta for the line of code formatted for position reviews.
        """
        return f"{line[:1]} Line {line_number} Side {side}: {line[1:]}\n"

    def get_pr_deltas_for_position_reviews(self, pr):
        """
        Gets the deltas for a pull request, formatted for position reviews.

        Args:
            pr (PullRequest): The pull request to get deltas for.

        Returns:
            str: The deltas for the pull request.
        """
        files_and_deltas = ""
        file_changes = pr.get_files()
        for f in file_changes:
            lines = f.patch.split("\n")
            deltas = ""
            files_and_deltas += f"{f.filename} - (deltas)\n"
            files_and_deltas += "-" * 30 + "\n"
            current_line = 0
            for line in lines:
                if line.startswith("@@"):
                    deltas += "-" * 30 + "\n"
                    deltas += f"{f.filename}\n"
                    deltas += line + "\n"
                else:
                    deltas += f"{line[:1]} Line {current_line}: {line[1:]}\n"
                current_line += 1

            files_and_deltas += f"{deltas}\n"
            files_and_deltas += "=" * 30 + "\n"

        return files_and_deltas

    def get_pr_comments(self, pr):
        """
        Gets the comments for a pull request.

        Args:
            pr (PullRequest): The pull request to get comments for.

        Returns:
            str: The comments for the pull request.
        """
        comments = ""
        issue_comments = pr.get_issue_comments()
        for c in issue_comments:
            comments += c.user.login + ": " + c.body + "\n"
        return comments

    def modify_pr(self, pr_num: int):
        """
        Modifies a pull request.

        Args:
            pr_num (int): The number of the pull request to modify.
        """
        pass

    def update_file_contents(
        self, filepath: str, new_contents: str, commit_message: str
    ):
        """
        Updates the contents of a file in the repository.

        Args:
            filepath (str): The path to the file.
            new_contents (str): The new contents of the file.
            commit_message (str): The commit message.
        """
        file = self.repo.get_contents(filepath)
        self.repo.update_file(file.path, commit_message, new_contents, file.sha)
