from typing import List, Dict, Tuple, Union
import asyncio

from openai import OpenAI
import openai
import langchain
from langchain.embeddings import OpenAIEmbeddings
from langchain.callbacks.streaming_aiter_final_only import (
    AsyncFinalIteratorCallbackHandler,
)
from langchain.text_splitter import (
    RecursiveCharacterTextSplitter,
    CharacterTextSplitter,
)
import numpy as np
from numpy.linalg import norm
from GPTsettings import GPTsettings

mapper = {
    "py": "python",
    "js": "js",
    "jsx": "js",
    "ts": "ts",
    "java": "java",
    "html": "html",
    "md": "markdown",
}


class GPTWrapper:
    def __init__(self, model: str = GPTsettings.MODEL) -> None:
        """
        Initializes a new instance of the GPTWrapper class.

        Args:
            model (str, optional): The name of the GPT model to use. Defaults to "text-davinci-002".
        """
        self.client = OpenAI()
        self.model: str = model
        self.conversation: list[
            dict[str, str]
        ] = []  # This will hold our conversation messages

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
            model=self.model, messages=self.conversation, max_tokens=max_tokens
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
        return "\n".join(
            f"{msg['role']}: {msg['content']}" for msg in self.conversation
        )


def to_docs(text: str, file_extension: str) -> List[str]:
    """
    Splits the input text into a list of documents based on the file extension.

    Args:
    text (str): The input text to be split.
    file_extension (str): The file extension of the input text.

    Returns:
    List[str]: A list of documents resulting from the split.
    """

    if file_extension in mapper:
        language = mapper[file_extension]
        python_splitter = RecursiveCharacterTextSplitter.from_language(
            language=language, chunk_size=500, chunk_overlap=0
        )
        docs = python_splitter.create_documents([text])
        docs = [doc.page_content for doc in docs]
    else:
        text_splitter = CharacterTextSplitter(
            separator="\n\n",
            chunk_size=500,
            chunk_overlap=0,
            length_function=len,
            is_separator_regex=False,
        )
        docs = text_splitter.split_text(text)
    return docs


def cosim(a, b):
    """
    Computes the cosine similarity between two vectors a and b.

    Args:
    a (numpy.ndarray): The first vector.
    b (numpy.ndarray): The second vector.

    Returns:
    float: The cosine similarity between a and b.
    """
    return np.dot(a, b) / (norm(a) * norm(b))


def cosim_matrix(a, b):
    """
    Computes the cosine similarity matrix between two matrices a and b.

    Args:
    a (numpy.ndarray): An array of shape (n).
    b (numpy.ndarray): A matrix of shape (k, n).

    Returns:
    numpy.ndarray: An array of shape (k) containing the cosine similarity between each row of a and b.

    Examples:
    >>> a = np.array([1, 2, 3])
    >>> b = np.array([[1, 2, 3], [4, 5, 6]])
    >>> cosim_matrix(a, b)
    array([1.        , 0.97463185])
    """
    return np.dot(a, b.T) / (norm(a) * norm(b, axis=1))


class FilesIndex:
    def __init__(self, files: dict[str, str]) -> None:
        """
        Initializes a new instance of the FilesIndex class.

        Args:
            files (dict[str, str]): A dictionary containing the files to be indexed.
            {Filename: FileContent}
        """
        self.files = files
        self.index: Dict[str, List[int]] = {}
        self.idx_to_filename = {}
        self.embeddings_model = OpenAIEmbeddings()
        self.docs: List[str] = []
        self.embeddings: np.ndarray = None
        self.index_files()

    def index_files(self) -> None:
        """
        Indexes the files in the `files` dictionary by creating a mapping between each file name and a list of document
        indices in the `docs` list. Also computes embeddings for each document and stores them in the `embeddings` array.
        """
        embeddings = []
        last_index = 0
        for filename, content in self.files.items():
            extension = filename.split(".")[-1]
            docs = to_docs(content, extension)
            idxs = list(range(last_index, last_index + len(docs)))
            last_index += len(docs)
            self.index[filename] = idxs
            self.docs += docs
            embeddings += self.embeddings_model.embed_documents(docs)
        self.embeddings = np.array(embeddings)
        for filename, idxs in self.index.items():
            for idx in idxs:
                self.idx_to_filename[idx] = filename

    def search_docs(
        self, queries: Union[str, List[str]], top_k: int = 5, sorted_by="score"
    ) -> Tuple[List[int], List[str]]:
        """
        Searches for the top k documents that are most similar to the given queries.


        Args:
            query (str): The query to search for.
            top_k (int, optional): The number of top documents to return. Defaults to 5.
            sorted_by (str, optional)
        Returns:
            List[str]: A list of the top k documents that are most similar to the given query.
        """
        if isinstance(queries, list):
            query_embeddings = []
            for query in queries:
                query_embeddings.append(
                    np.array(self.embeddings_model.embed_query(query))
                )

            scores = cosim_matrix(query_embeddings[0], self.embeddings)
            for query_embedding in query_embeddings[1:]:
                scores += cosim_matrix(query_embedding, self.embeddings)

        elif isinstance(queries, str):
            query_embedding = np.array(self.embeddings_model.embed_query(query))
            scores = cosim_matrix(query_embedding, self.embeddings)

        top_k_idxs = np.argsort(scores)[::-1][:top_k]

        if sorted_by == "file":
            top_k_idxs = sorted(top_k_idxs)

        return top_k_idxs, [self.docs[idx] for idx in top_k_idxs]

    def get_filename(self, idx: int):
        return self.idx_to_filename[idx]
