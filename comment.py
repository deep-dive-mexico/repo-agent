from github import Auth, Github
import os
from openai import OpenAI
import openai
import os
import json
import re

ACCESS_TOKEN = os.environ["GH_ACCESSTOKEN"]
branch_or_prnum = os.environ["PR_NUMBER"] # On pull request open (number), on push (branch name)
repo_name = os.environ["REPO_NAME"]
openai.api_key = os.environ["OPENAI_API_KEY"]
MAIN_BRANCH = os.environ["MAIN_BRANCH"].split('/')[-1]

class GithubHandler:

    def __init__(self, repo_name: str = "deep-dive-mexico/aladdin-repo"):
        self.repo_name = repo_name

        # Authenticate
        self.authenticate()
        # get repo
        self.repo = self.g.get_repo(self.repo_name)
        # get prs
        self.prs = self.repo.get_pulls(state = "open", sort = "created", base = MAIN_BRANCH)
        # get prs numbers
        self.prs_nums = [pr.number for pr in self.prs]
        # create dict of prs
        self.prs_dict = {k: v for k, v in zip(self.prs_nums, self.prs)}

        # get all files
        # self.get_all_files()

    def get_commits(self):
        # Loop through each pull request by number
        for pr_num in self.prs_dict:
            pr = self.prs_dict[pr_num]["pr"]
            # Get the list of commits for the pull request
            commits = pr.get_commits()
            commit_list = []

            for commit in commits:
                # Get the commit object by its SHA
                commit_obj = self.repo.get_commit(commit.sha)
                commit_data = {
                    'sha': commit.sha,
                    'files': []
                }
                # Loop through each file in the commit object
                for file in commit_obj.files:
                    # Store filename, status, and patch of the file
                    file_data = {
                        'filename': file.filename,
                        'status': file.status,
                        'patch': file.patch
                    }
                    commit_data['files'].append(file_data)

                # Append commit data to the commit list
                commit_list.append(commit_data)

            # Save the list of commit data in the dictionary under the corresponding PR number
            self.prs_dict[pr_num]["commits"] = commit_list

    def authenticate(self):
        # github authentication
        self.auth = Auth.Token(os.environ['GH_ACCESSTOKEN'])
        self.g = Github(auth=self.auth)

    def get_latest_pr_from_branch(self, branch: str, downstream_branch: str = None):
        prs = self.repo.get_pulls(state = "open", sort = "created", base=branch, direction="desc")
        if downstream_branch:
            for pr in prs:
                if pr.head.ref == downstream_branch:
                    return pr
        return prs[0]
    
    def get_pr_from_id(self, pr_id: int):
        pr = self.repo.get_pull(pr_id)
        return pr
    
    def get_all_files(self):
        contents = self.repo.get_contents("")

        while contents:
            file_content = contents.pop(0)
            if file_content.type == "dir":
                contents.extend(self.repo.get_contents(file_content.path))
            
        self.all_files = contents

    def get_pr_deltas(self, pr):
        files_and_deltas = ""
        file_changes = pr.get_files()
        for f in file_changes:
            files_and_deltas += f.filename + "\n" + f.patch + "\n"
        return files_and_deltas

    def get_pr_deltas_new(self, pr):
            files_and_deltas = ""
            file_changes = pr.get_files()
            for f in file_changes:
                lines = f.patch.split('\n') 
                current_line = 0  
                deltas = ""
                for line in lines:
                    # Check for chunk header and calculate line changes
                    if line.startswith('@@'):
                        files_and_deltas += '-'*30 + '\n'
                        add_start, add_len = list(map(int, re.findall("\+(.+?) ", line)[0].split(',')))
                        rem_start, rem_len = list(map(int, re.findall("\-(.+?) ", line)[0].split(',')))
                        chunk_len = max(add_len, rem_len)
                        deltas += line 
                        current_line = add_start  # Set current line to start of 'added' lines part
                    else:
                        if line.startswith('+') or line.startswith('-'):
                            deltas += f"{line[:1]} Line {current_line}: {line[1:]}\n"
                        else:
                            deltas += line + '\n'

                        # Increment line for only added lines and normal lines, not removed
                        if not line.startswith('-'):
                            current_line += 1

                files_and_deltas += f'{f.filename}\n{deltas}\n'
                files_and_deltas += '-'*30 + '\n'

            return files_and_deltas
        
    def get_pr_deltas_new_2(self, pr):
        files_and_deltas = ""
        file_changes = pr.get_files()
        for f in file_changes:
            lines = f.patch.split('\n') 
            current_line = 0  
            deltas = ""
            current_line = 0
            for line in lines:
                # Check for chunk header and calculate line changes
                if line.startswith('@@'):
                    deltas += f'{f.filename}\n'
                    deltas += '-'*30 + '\n'
                    deltas += line 
                else:
                    deltas += f"{line[:1]} Line {current_line}: {line[1:]}\n"
                current_line += 1

            files_and_deltas += f'{deltas}\n'
            files_and_deltas += '-'*30 + '\n'

        return files_and_deltas


    def get_pr_comments(self, pr):
        comments = ""
        issue_comments = pr.get_issue_comments()
        for c in issue_comments:
            comments += c.user.login + ": " + c.body + "\n"
        return comments


    def modify_pr(self, pr_num: int):
        pass

    def get_file_contents(self, filepath: str):
        file_contents = self.repo.get_contents(filepath)
        return file_contents.decoded_content.decode()
    
    def update_file_contents(self, filepath: str, new_contents: str, commit_message: str):
        # Get the file that you want to update from the repo
        file = self.repo.get_contents(filepath)

        self.repo.update_file(file.path, commit_message, new_contents, file.sha)

class GPTWrapper:
    def __init__(self, model="gpt-4"):
        self.client = OpenAI()
        self.model = model
        self.conversation = []  # This will hold our conversation messages
    
    def add_message(self, role, content):
        # Add a message to the conversation
        self.conversation.append({"role": role, "content": content})
    
    def edit_message(self, index, role, content):
        # Edit a message in the conversation by index
        if index < len(self.conversation):
            self.conversation[index] = {"role": role, "content": content}
        else:
            raise IndexError("Message index out of range")
    
    def get_response(self, max_tokens=1000):
        # Get a response from the API based on the current conversation
        response = self.client.chat.completions.create(
            model=self.model,
            messages=self.conversation,
            max_tokens=max_tokens
        )
        
        # Extract and return the content from the response
        return response.choices[0].message.content
    
    def __getitem__(self, index):
        # Allow getting a message by index
        return self.conversation[index]
    
    def __setitem__(self, index, value):
        # Allow setting a message by index
        role, content = value
        self.edit_message(index, role, content)
    
    def __str__(self):
        # Return a readable string representation of the conversation
        return "\n".join(f"{msg['role']}: {msg['content']}" for msg in self.conversation)


class ResponseParser():
    def __init__(self, response):
        self.python_code = self.parse_python_code(response)

    def parse_python_code(self, llm_answer):
        llm_answer = re.sub(r"```python", "", llm_answer)
        llm_answer = re.sub(r"```", "", llm_answer)
        llm_answer = re.sub(r"\\n", "\n", llm_answer)
        return llm_answer

    def get_body_event_and_comments(self):
        locals_dict = {}
        exec(self.python_code, locals_dict)
        return locals_dict['body'], locals_dict['event'], locals_dict['comments']

def is_branch(candidate):
    try:
        _ = int(candidate)
        return False
    except:
        return True

if __name__ == "__main__":

    comment_only = False
    
    GH = GithubHandler(repo_name=os.environ['REPO_NAME'])
    GPT = GPTWrapper()
    GPT.add_message("system", "You are a sassy Senior developer, who hates the fact that his job is only reviewing PRs, but just so happens, that he makes the best reviews, and always provides good advice, best practices and laughs at coe that is inneficient, but still provides a way to optimize it. You are a Senior Developer, and you are the best at what you do. Remember to keep your responses somewhat short in your messages, every word you write is another precious moment at this job, so keep it short.")
    
    if is_branch(branch_or_prnum):
        pr = GH.get_latest_pr_from_branch(MAIN_BRANCH, branch_or_prnum)
    else:
        pr = GH.get_pr_from_id(int(branch_or_prnum))
    pr_deltas = GH.get_pr_deltas_new_2(pr)
    
    GPT.add_message("user", "Provide a code review for this Pull Request:\n```python\n" + pr_deltas + "\n```\n")

    comments = GH.get_pr_comments(pr)
    GPT.add_message("user", f"Here are comments from the PR:\n{comments}")
    
    if comment_only:
        message_response = GPT.get_response(max_tokens=1000)
        print('Commenting on PR with response...')
        pr.create_issue_comment(message_response)

    # Do a code review -- Experimental
    else:
        for _ in range(3):
            message_format = '''Return your comments in the following format, it needs to be python code!
            body = # Your main comment here
            event = # One of the following: "COMMENT", "REQUEST_CHANGES", "PENDING", "APPROVE"
            comments = [
            {
                'path': 'my_file.py',    # adjust according to your files. USE THE FULL PATH
                'position': position,           # adjust according to your diff, 
                'body': 'You should consider refactoring this piece of code.'
            },
            {
                'path': 'another_file.py',    # adjust according to your files. USE THE FULL PATH
                'position': position,                # adjust according to your diff
                'body': 'This part could be optimized.'
            },
            ]
            when specifying the positions, ONLY USE THE LINES THAT HAVE CHANGED, the appear like + LINE or - LINE, be really careful with this! Don't use the ' token in your comments (only when needed for the code) or it will break the python code.

            ONLY return the code
            '''
            GPT.add_message("user", message_format)
            message_response = GPT.get_response(max_tokens=2500)
            # Parse the response
            try:
                response_parser = ResponseParser(message_response)
                body, event, comments = response_parser.get_body_event_and_comments()
                breakpoint()

                # Create a review
                print('Creating review...')
                pr.create_review(body=body, event=event, comments=comments)
                break
            except Exception as e:
                print(e)
                print('Error parsing response, try again')
                continue