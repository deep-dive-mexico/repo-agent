from github import Auth, Github
import github
import os
from openai import OpenAI
import openai
import os
import json
import re
import traceback
# Local Imports
from GPTsettings import GPTsettings
import logging
from typing import List

ACCESS_TOKEN = os.environ["GH_ACCESSTOKEN"]
BRANCH_OR_PR_NUMBER = os.environ["PR_NUMBER"] # On pull request open (number), on push (branch name)
REPO_NAME = os.environ["REPO_NAME"]
openai.api_key = os.environ["OPENAI_API_KEY"]
MAIN_BRANCH = os.environ["MAIN_BRANCH"].split('/')[-1]


class GithubHandler:
    """
    Handles interactions with the GitHub API.
    """
    def __init__(self, repo_name: str):
        """
        Initializes a new instance of the GithubHandler class.

        Args:
            repo_name (str): The name of the repository to interact with.
        """
        self.repo_name = repo_name
        self.authenticate()
        self.repo = self.g.get_repo(self.repo_name)
        self.prs = self.repo.get_pulls(state = "open", sort = "created", base = MAIN_BRANCH)
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
                commit_data = {
                    'sha': commit.sha,
                    'files': []
                }

                for file in commit_obj.files:
                    file_data = {
                        'filename': file.filename,
                        'status': file.status,
                        'patch': file.patch
                    }
                    commit_data['files'].append(file_data)

                commit_list.append(commit_data)

            self.prs_dict[pr_num]["commits"] = commit_list

    def authenticate(self):
        """
        Authenticates with the GitHub API.
        """
        self.auth = Auth.Token(os.environ['GH_ACCESSTOKEN'])
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
        prs = self.repo.get_pulls(state = "open", sort = "created", base=branch, direction="desc")
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
            files_and_deltas += full_file_delta + '\n' + file_delta + '\n'

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
        return f'{filename} - (full file before changes - this is just for context, DONT REVIEW THIS CODE, ITs Not part of the PR)\n' + '-'*30 + '\n' + file_full_content + '\n' + '-'*30 + '\n'

    def get_file_delta(self, patch, filename):
        """
        Gets the deltas for a file in the repository.

        Args:
            patch (str): The patch for the file.
            filename (str): The path to the file.

        Returns:
            str: The deltas for the file formatted for position reviews.
        """
        lines = patch.split('\n')
        current_line = 0
        deltas = ""

        deltas += f'{filename} - (deltas - This is the code you need to review)\n'
        deltas += '-'*30 + '\n'

        for line in lines:
            if line.startswith('@@'):
                deltas += self.get_chunk_header(line)
                add_start, add_len = list(map(int, re.findall("\+(.+?) ", line)[0].split(',')))
                rem_start, rem_len = list(map(int, re.findall("\-(.+?) ", line)[0].split(',')))
                chunk_len = max(add_len, rem_len)
                current_add_line = add_start
                current_rem_line = rem_start
            else:
                if line.startswith('-'):
                    deltas += self.get_line_delta(line, current_rem_line, 'LEFT')
                    current_rem_line += 1
                else:
                    deltas += self.get_line_delta(line, current_add_line, 'RIGHT')
                    current_add_line += 1

        return f'{filename}\n{deltas}\n' + '-'*30 + '\n'

    def get_chunk_header(self, line):
        """
        Gets the header for a chunk of code.

        Args:
            line (str): The line containing the chunk header.

        Returns:
            str: The chunk header formatted for position reviews.
        """
        return '-'*30 + '\n' + f'{line}\n'

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
            lines = f.patch.split('\n')
            deltas = ""
            files_and_deltas += f'{f.filename} - (deltas)\n'
            files_and_deltas += '-'*30 + '\n'
            current_line = 0
            for line in lines:
                if line.startswith('@@'):
                    deltas += '-'*30 + '\n'
                    deltas += f'{f.filename}\n'
                    deltas += line + '\n'
                else:
                    deltas += f"{line[:1]} Line {current_line}: {line[1:]}\n"
                current_line += 1

            files_and_deltas += f'{deltas}\n'
            files_and_deltas += '='*30 + '\n'

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

    def update_file_contents(self, filepath: str, new_contents: str, commit_message: str):
        """
        Updates the contents of a file in the repository.

        Args:
            filepath (str): The path to the file.
            new_contents (str): The new contents of the file.
            commit_message (str): The commit message.
        """
        file = self.repo.get_contents(filepath)
        self.repo.update_file(file.path, commit_message, new_contents, file.sha)

class GPTWrapper:
    def __init__(self, model: str = GPTsettings.MODEL) -> None:
        """
        Initializes a new instance of the GPTWrapper class.

        Args:
            model (str, optional): The name of the GPT model to use. Defaults to "text-davinci-002".
        """
        self.client = OpenAI()
        self.model: str = model
        self.conversation: list[dict[str, str]] = []  # This will hold our conversation messages
    
    def add_message(self, role: str, content: str) -> None:
        """
        Adds a message to the conversation history.

        Args:
            role (str): Indicates who is acting on the conversation [system, user, assistant].
            content (str): The actual message.
        """
        self.conversation.append({"role": role, "content": content})
    
    def edit_message(self, index: int, role: str, content: str) -> None:
        """
        Edits a message in the conversation by index.

        Args:
            index (int): The index of the message to edit.
            role (str): Indicates who is acting on the conversation [system, user, assistant].
            content (str): The new content of the message.

        Raises:
            IndexError: If the index is out of range.
        """
        if index < len(self.conversation):
            self.conversation[index] = {"role": role, "content": content}
        else:
            raise IndexError("Message index out of range")
    
    def get_response(self, max_tokens: int = 1000) -> str:
        """
        Gets a response from the API based on the current conversation.

        Args:
            max_tokens (int, optional): The maximum number of tokens to generate in the response. Defaults to 1000.

        Returns:
            str: The generated response.
        """
        response = self.client.chat.completions.create(
            model=self.model,
            messages=self.conversation,
            max_tokens=max_tokens
        )
        
        return response.choices[0].message.content
    
    def __getitem__(self, index: int) -> dict[str, str]:
        """
        Gets a message from the conversation by index.

        Args:
            index (int): The index of the message to get.

        Returns:
            dict[str, str]: The message at the specified index.
        """
        return self.conversation[index]
    
    def __setitem__(self, index: int, value: tuple[str, str]) -> None:
        """
        Sets a message in the conversation by index.

        Args:
            index (int): The index of the message to set.
            value (tuple[str, str]): A tuple containing the role and content of the new message.
        """
        role, content = value
        self.edit_message(index, role, content)
    
    def __str__(self) -> str:
        """
        Returns a readable string representation of the conversation.

        Returns:
            str: A string representation of the conversation.
        """
        return "\n".join(f"{msg['role']}: {msg['content']}" for msg in self.conversation)

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
            raise ParsingError('No python snippet provided')

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
        except Exception as e:
            raise ParsingError('Error executing python code')
        
        return locals_dict['body'], locals_dict['event'], self.verify_comments_comply(locals_dict['comments'])
    
    def verify_comments_comply(self, comments: List[dict]):
        """Verifies comment dicts comply with the expected format

        Args:
            comments (List[dict]): A list of comment dicts
        """
        new_comments = []
        for comment in comments:
            new_comment = comment.copy()
            if new_comment['side'] not in ['LEFT', 'RIGHT']:
                new_comment.pop(new_comment['side'])
            if "start_line" in new_comment:
                if new_comment["start_line"] is None:
                    new_comment.pop("start_line")
            if "start_side" in new_comment:
                if new_comment["start_side"] is None:
                    new_comment.pop("start_side")
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
    def __init__(self, repo_name: str, branch_or_prnum: str, comment_only: bool = False) -> None:
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
        """
        self.comment_only = comment_only
        self.GH = GithubHandler(repo_name=repo_name)
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
            return self.GH.get_latest_pr_from_branch(MAIN_BRANCH, branch_or_prnum)
        else:
            return self.GH.get_pr_from_id(int(branch_or_prnum))

    def add_pr_messages(self) -> None:
        """
        Adds messages to the GPTWrapper instance related to the pull request.
        """
        pr_deltas = self.GH.get_pr_deltas(self.pr)
        self.GPT.add_message("user", GPTsettings.USER_INSTRUCTIONS.format(pr_title=self.pr.title, pr_user=self.pr.user, pr_deltas=pr_deltas))
        comments = self.GH.get_pr_comments(self.pr)
        self.GPT.add_message("user", GPTsettings.COMMENTS_PROMPT.format(comments=comments))

    def comment_on_pr(self) -> None:
        """
        Comments on the pull request with the response from the GPTWrapper instance.
        """
        message_response = self.GPT.get_response(max_tokens=1000)
        logging.info('Commenting on PR with response...')
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
            message_response = self.GPT.get_response(max_tokens=2500)
            try:
                logging.info(message_response)
                response_parser = ResponseParser(message_response)
                body, event, comments = response_parser.get_body_event_and_comments()
                self.pr.create_review(body=body, event=event, comments=comments)
                return True
            except ParsingError as e:
                error = traceback.format_exc()
                logging.error(error)
                self.GPT.add_message("user", f'Your code failed with exception {error} try again')
                logging.error('Error parsing response, try again')
                continue
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

if __name__ == "__main__":
    agent = CommentAgent(repo_name=REPO_NAME, branch_or_prnum=BRANCH_OR_PR_NUMBER)
    agent.run()
