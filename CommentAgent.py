import github
import re
import traceback
from typing import List
import logging

# Local Imports
from GPTsettings import GPTsettings
from GithubHandlers import GithubHandler
from GPTutils import GPTWrapper


class ParsingError(Exception):
    """
    An exception raised when an error occurs while parsing a response.
    """

    pass


class ResponseParser:
    """
    A class used to parse a response and extract a Python code block.

    Attributes
    ----------
    python_code : str
        The Python code block extracted from the response.

    Methods
    -------
    parse_python_code(llm_answer):
        Extracts the Python code block from the response using a regex pattern.
    get_body_event_and_comments():
        Executes the Python code block and returns the values of 'body', 'event', and 'comments'.
    """

    def __init__(self, response):
        self.python_code = self.parse_python_code(response)

    def parse_python_code(self, llm_answer):
        """
        Extracts the Python code block from the response using a regex pattern.

        Parameters
        ----------
        llm_answer : str
            The response to be parsed.

        Returns
        -------
        str
            The Python code block extracted from the response.

        Raises
        ------
        Exception
            If no Python code block is found in the response.
        """
        pattern = r"```python\n([\s\S]*?)\n```"
        match = re.search(pattern, llm_answer)
        if match:
            python_code_block = match.group(1)
            print("Found Python Block: ", python_code_block)
            return python_code_block
        else:
            raise ParsingError(
                "No python snippet provided. Please include a python code block using the markdown format ```python ... ``` for clarity and proper parsing."
            )

    def get_body_event_and_comments(self):
        """
        Executes the Python code block and returns the values of 'body', 'event', and 'comments'.

        Returns
        -------
        tuple
            A tuple containing the values of 'body', 'event', and 'comments'.
        """
        locals_dict = {}
        try:
            exec(self.python_code, locals_dict)
        except Exception:
            raise ParsingError("Error executing python code")

        return (
            locals_dict["body"],
            locals_dict["event"],
            self.verify_comments_comply(locals_dict["comments"]),
        )

    def verify_comments_comply(self, comments: List[dict]):
        """Verifies comment dicts comply with the expected format

        Args:
            comments (List[dict]): A list of comment dicts
        """
        new_comments = []
        for comment in comments:
            # Remove keys that are not needed for the review
            new_comment = comment.copy()
            if new_comment["side"] not in ["LEFT", "RIGHT"]:
                new_comment.pop(new_comment["side"])
            if "start_line" in new_comment:
                if new_comment["start_line"] is None:
                    new_comment.pop("start_line")
            if "start_side" in new_comment:
                if new_comment["start_side"] is None:
                    new_comment.pop("start_side")

            # Remove leading spaces from comment body
            comment_body = new_comment["body"]
            comment_lines = comment_body.split("\n")
            new_lines = []
            for line in comment_lines:
                if "```suggestion" in line or "```" in line:
                    line = line.lstrip()
                new_lines.append(line)
            new_comment["body"] = "\n".join(new_lines)
            new_comments.append(new_comment)
        return new_comments


class CommentAgent:
    """
    A class that represents an agent that can comment on or review a pull request.

    Attributes:
    -----------
    comment_only : bool
        A boolean indicating whether the agent should only comment on the pull request.
    GH : GithubHandler
        An instance of the GithubHandler class.
    pr : github.PullRequest.PullRequest
        An instance of the PullRequest class representing the pull request.
    GPT : GPTWrapper
        An instance of the GPTWrapper class.
    """

    def __init__(
        self,
        repo_name: str,
        branch_or_prnum: str,
        comment_only: bool = False,
        main_branch="main",
        github_auth_token: str = None,
    ) -> None:
        """
        Initializes a CommentAgent instance.

        Parameters:
        -----------
        repo_name : str
            The name of the repository.
        branch_or_prnum : str
            The branch name or pull request number.
        comment_only : bool, optional
            A boolean indicating whether the agent should only comment on the pull request.
        main_branch : str, optional (default: "main")
            The name of the main branch.
        github_auth_token : str, optional
            The GitHub authentication token.
        """
        self.comment_only = comment_only
        self.main_branch = main_branch
        self.GH = GithubHandler(
            repo_name=repo_name, main_branch=main_branch, auth_token=github_auth_token
        )
        self.GH.FILE_EXTENSIONS = GPTsettings.FILE_EXTENSIONS
        self.pr = self.get_pr(branch_or_prnum)
        self.init_GPT()

    @staticmethod
    def is_branch(branch_or_prnum: str) -> bool:
        """
        Determines whether the given string is a branch name or a pull request number.

        Parameters:
        -----------
        branch_or_prnum : str
            The branch name or pull request number.

        Returns:
        --------
        bool
            True if the string is a branch name, False if it is a pull request number.
        """
        try:
            _ = int(branch_or_prnum)
            return False
        except ValueError:
            return True

    def init_GPT(self) -> None:
        """
        Initializes the GPTWrapper instance.
        """
        self.GPT = GPTWrapper()
        self.GPT.add_message("system", GPTsettings.SYSTEM_PROMPT)
        self.add_pr_messages()

    def get_pr(self, branch_or_prnum: str) -> github.PullRequest.PullRequest:
        """
        Gets the pull request instance from the given branch name or pull request number.

        Parameters:
        -----------
        branch_or_prnum : str
            The branch name or pull request number.

        Returns:
        --------
        github.PullRequest.PullRequest
            An instance of the PullRequest class representing the pull request.
        """
        if self.is_branch(branch_or_prnum):
            return self.GH.get_latest_pr_from_branch(self.main_branch, branch_or_prnum)
        else:
            return self.GH.get_pr_from_id(int(branch_or_prnum))

    def add_pr_messages(self) -> None:
        """
        Adds messages to the GPTWrapper instance related to the pull request.
        """
        pr_deltas = self.GH.get_pr_deltas(self.pr)
        self.GPT.add_message(
            "user",
            GPTsettings.USER_INSTRUCTIONS.format(
                pr_title=self.pr.title, pr_user=self.pr.user, pr_deltas=pr_deltas
            ),
        )
        comments = self.GH.get_pr_comments(self.pr)
        self.GPT.add_message(
            "user", GPTsettings.COMMENTS_PROMPT.format(comments=comments)
        )

    def comment_on_pr(self) -> None:
        """
        Comments on the pull request with the response from the GPTWrapper instance.
        """
        message_response = self.GPT.get_response(max_tokens=1000)
        logging.info("Commenting on PR with response...")
        self.pr.create_issue_comment(message_response)

    def review_pr(self) -> bool:
        """
        Reviews the pull request with the response from the GPTWrapper instance.

        Returns:
        --------
        bool
            True if the review was successful, False otherwise.
        """
        for _ in range(3):
            message_format = GPTsettings.MESSAGE_FORMAT
            self.GPT.add_message("user", message_format)
            message_response = self.GPT.get_response(
                max_tokens=4096
            )  # Max length of response is 4096 (max allowed for responses by openai)
            try:
                logging.info(message_response)
                response_parser = ResponseParser(message_response)
                body, event, comments = response_parser.get_body_event_and_comments()
                self.pr.create_review(body=body, event=event, comments=comments)
                print(self.GPT)
                return True
            except ParsingError:
                error = traceback.format_exc()
                logging.error(error)
                self.GPT.add_message(
                    "user", f"Your code failed with exception {error} try again"
                )
                logging.error("Error parsing response, try again")
                continue
            except github.GithubException:
                error = traceback.format_exc()
                logging.error(f"Github Exception:\n{error}")
                self.GPT.add_message(
                    "user", f"Your code failed with exception {error} try again"
                )
        return False

    def run(self) -> None:
        """
        Runs the CommentAgent instance.
        """
        if self.comment_only:
            self.comment_on_pr()
        else:
            success = self.review_pr()
            if not success:
                self.init_GPT()
                self.comment_on_pr()


def get_unique_list_items(list_: list):
    unique_list = []
    for item in list_:
        if item not in unique_list:
            unique_list.append(item)
    return unique_list
