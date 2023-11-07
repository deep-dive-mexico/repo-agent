from github import Auth, Github
import os
import openai
import os
import json


class GithubHandler:

    def __init__(self, repo_name: str = "deep-dive-mexico/aladdin-repo"):
        self.repo_name = repo_name

        # Authenticate
        self.authenticate()
        # get repo
        self.repo = self.g.get_repo(self.repo_name)
        # get prs
        self.prs = self.repo.get_pulls(state = "open", sort = "created", base = "main")
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
        self.auth = Auth.Token(ACCESS_TOKEN)
        self.g = Github(auth=self.auth)

    def get_latest_pr_from_branch(self, branch: str):
        prs = self.repo.get_pulls(state = "open", sort = "created", base=branch, direction="desc")
        return prs[0]

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
    def __init__(self, model="gpt-4-1106-preview"):
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
        response = openai.ChatCompletion.create(
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

if __name__ == "__main__":
    ACCESS_TOKEN = os.environ["GH_ACCESSTOKEN"]
    #branch_or_prnum = os.environ["PR_NUMBER"] # On pull request open (number), on push (branch name)
    repo_name = os.environ["REPO_NAME"]
    openai.api_key = os.environ["OPENAI_API_KEY"]
    
    GH = GithubHandler(repo_name=repo_name)
    GPT = GPTWrapper()
    GPT.add_message("system", "You are a sassy Senior developer, who hates the fact that his job is only reviewing PRs, but just so happens, that he makes the best reviews, and always provides good advice, best practices and laughs at coe that is inneficient, but still provides a way to optimize it. You try to be consize, but sometimes you just can't help yourself. You are a Senior Developer, and you are the best at what you do.")

    # Get the latest PR from the branch
    pr = GH.get_latest_pr_from_branch('main')
    pr_deltas = GH.get_pr_deltas(pr)
    GPT.add_message("user", "Provide a code review for this Pull Request:\n```python\n" + pr_deltas + "\n```\n")

    comments = GH.get_pr_comments(pr)
    GPT.add_message("user", f"Here are comments from the PR:\n{comments}")
    
    message_response = GPT.get_response(max_tokens=1000)
    print(message_response)
    
    # Comment the response on the PR
    print('Commenting on PR with response...')
    pr.create_issue_comment(message_response)
   