from llama_index import (
    VectorStoreIndex,
    SummaryIndex,
    SimpleDirectoryReader,
    ServiceContext
)
from llama_index.agent import OpenAIAgent
from llama_index.schema import IndexNode
from llama_index.tools import QueryEngineTool, ToolMetadata
from llama_index.llms import OpenAI
from llama_index.retrievers import RecursiveRetriever
from llama_index.query_engine import RetrieverQueryEngine
from llama_index.response_synthesizers import get_response_synthesizer


class Retriever:
    """
    A class to handle the retrieval and querying of documents using Llama Index services.

    Attributes:
        llm (OpenAI): An instance of the OpenAI model.
        service_context (ServiceContext): The context for the service.
        docs (dict): A dictionary holding the loaded documents.
        query_engine (RetrieverQueryEngine): The query engine for retrieval tasks.
    """

    def __init__(self, index_path):
        """
        Initialize the Retriever with a specified index path.

        Args:
            index_path (str): The path to the index directory.
        """
        self.llm = OpenAI(temperature=0, model="gpt-4-1106-preview")
        self.service_context = ServiceContext.from_defaults(llm=self.llm)
        self.docs = {}
        self.query_engine = None

    def load_documents(self, files):
        """
        Load documents from the given file paths.

        Args:
            files (list): A list of file paths to load.
        """
        for file in files:
            self.docs[file] = SimpleDirectoryReader(
                input_files=[f"./{file}"]
            ).load_data()

    def create_agents(self):
        """
        Create agents for each loaded document.
        """
        self.agents = {}
        for file, doc in self.docs.items():
            summary_index = SummaryIndex.from_documents(doc, service_context=self.service_context)
            list_query_engine = summary_index.as_query_engine()

            query_engine_tools = [
                QueryEngineTool(
                    query_engine=list_query_engine,
                    metadata=ToolMetadata(name="summary_tool", description=f"Retrieve context from {file}")
                )
            ]

            function_llm = OpenAI(model="gpt-4-1106-preview")
            self.agents[file] = OpenAIAgent.from_tools(query_engine_tools, llm=function_llm, verbose=True)

    def create_query_engine(self, agents):
        """
        Create a query engine using the specified agents.

        Args:
            agents (dict): A dictionary of agents to be used in the query engine.
        """
        nodes = [
            IndexNode(text=f"Law Information about {file}", index_id=file)
            for file in self.docs.keys()
        ]

        vector_index = VectorStoreIndex(nodes)
        vector_retriever = vector_index.as_retriever(similarity_top_k=1)
        recursive_retriever = RecursiveRetriever(
            "vector", retriever_dict={"vector": vector_retriever}, query_engine_dict=agents, verbose=True
        )
        
        response_synthesizer = get_response_synthesizer(response_mode="compact")
        self.query_engine = RetrieverQueryEngine.from_args(
            recursive_retriever, response_synthesizer=response_synthesizer, service_context=self.service_context
        )
