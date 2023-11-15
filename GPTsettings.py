class GPTsettings:
    MODEL = "gpt-4-1106-preview"
    SYSTEM_PROMPT = "You are a senior developer and will provide the best and more concise code reviews. You follow best practices and  provide suggestions (using the ```suggestion \n <your_suggestion> ``` format) for code changes. "
    USER_INSTRUCTIONS = """{repo_instructions}Provide a code review for this Pull Request titled '{pr_title}' created by '{pr_user.login}':
    Some desired properties of your review:
    - Provide code suggestions as much as possible in your comments, for that you can use the standart format ```suggestion \n <suggested_change> \n```
    - When providing suggestions use the lines that you want to modify! If you select a range the suggestion will modify all that range for your suggested code, if you only add one line all the code from your suggestion will be inserted in that line.
    - Add documentation to functions when needed.
    - Propose optimized code when possible.
    - Be really careful with the suggestions! Good luck
    - if applicable, follow practices defined in the project
    
    
    
    ```
    {pr_deltas}
    ```
    """
    COMMENTS_PROMPT = "Here are comments from the PR:\n{comments}"

    MESSAGE_FORMAT = """Return your comments in the following format, it needs to be python code!
```python
body = # Your main comment here
event = # One of the following: "COMMENT", "REQUEST_CHANGES", "PENDING", "APPROVE"
comments = [
{
    'path': 'my_file.py',         # adjust according to your files. USE THE FULL PATH
    'side': 'your_side',                 # In a split diff view, the side of the diff that the pull request's changes appear on. 
                                    # Can be LEFT or RIGHT. Use LEFT for deletions that appear in red (-). 
                                    # Use RIGHT for additions that appear in green (+) or unchanged lines that appear in white and are shown for context.
                                    # For a multi-line comment, side represents whether the last line of the comment range is a deletion or addition. 

    'line': 'your_line',                 # The line of the blob in the pull request diff that the comment applies to.
                                    # For a multi-line comment, the last line of the range that your comment applies to.

    'start_line': 'your_start_line',     # The start_line is the first line in the pull request diff that your multi-line comment applies to. 
                                    # Optional, only needed for multi-line comments, If not needed just don't add the parameter to the dict. 
                                    # If used it needs to be strictly smaller than line
                                    # DONT USE NONE, 

    'start_side': 'your_start_side',     # The start_side is the starting side of the diff that the comment applies to. Can be LEFT or RIGHT.
                                    # Optional, only needed for multi-line comments, If not needed just don't add the parameter to the dict
                                    # DONT USE NONE 

    'body': '''You should consider refactoring this piece of code, for example you could try:
    ```suggestion
    def a_func(a:int, b:int):
        # func content
        return # something
    ```
    ''' # You should follow best style practices for the code language that you are working with, But the body string should be a valid python multiline string
},
{
    'path': 'another_file.py',    # adjust according to your files. USE THE FULL PATH
    ...
},
...
]
```

ONLY RETURN THE CODE and use ```python\n<valid_python_code>\n```"
"""
    FILE_EXTENSIONS = {
        ".py",
        ".java",
        ".js",
        ".ts",
        ".html",
        ".css",
        ".scss",
        ".sass",
        ".gql",
        ".graphql",
        ".sql",
        ".md",
        ".jsx",
    }
