from llama_index import (
    VectorStoreIndex,
    SummaryIndex,
    SimpleKeywordTableIndex,
    SimpleDirectoryReader,
    ServiceContext,
)
from llama_index.agent import OpenAIAgent
from llama_index.schema import IndexNode
from llama_index.tools import QueryEngineTool, ToolMetadata
from llama_index.llms import OpenAI
import os
from llama_index.retrievers import RecursiveRetriever
from llama_index.query_engine import RetrieverQueryEngine
from llama_index.response_synthesizers import get_response_synthesizer

class Retriever:
    def __init__(self, index_path):
        
        self.llm = OpenAI(temperature=0, model="gpt-4-1106-preview")
        self.service_context = ServiceContext.from_defaults(llm=self.llm)
    
    def load_documents(self, files):
        self.docs = {}
        for file in files.keys():
            docs[file] = SimpleDirectoryReader(
                input_files=[f"./{file}"],
            ).load_data()



    def create_agents(self, files, docs, service_context):
            summary_index = SummaryIndex.from_documents(docs[file], service_context=service_context)
            
            # define query engines
            vector_query_engine = vector_index.as_query_engine()
            list_query_engine = summary_index.as_query_engine()
            
            # define tools
            query_engine_tools = [
                QueryEngineTool(
                    query_engine=vector_query_engine,
                    metadata=ToolMetadata(name="vector_tool", description=f"Useful for summarization questions related to {file}"),
                ),
                QueryEngineTool(
                    query_engine=list_query_engine,
                    metadata=ToolMetadata(name="summary_tool", description=f"Useful for retrieving specific context from {file}"),
                ),
            ]
            
            # build agent
            function_llm = OpenAI(model="gpt-4-1106-preview")
            agent = OpenAIAgent.from_tools(query_engine_tools, llm=function_llm, verbose=True)
            
            agents[file] = agent
    
    def create_query_engine(_files, _agents, _service_context):
        nodes = []
        for file in files.keys():
            file_summary = f"This content contains Law Information  about {file}. Use this index if you need to lookup specific facts about {file}.\n"
            node = IndexNode(text=file_summary, index_id=file)
            nodes.append(node)

        vector_index = VectorStoreIndex(nodes)
        vector_retriever = vector_index.as_retriever(similarity_top_k=1)
        recursive_retriever = RecursiveRetriever(
            "vector", retriever_dict={"vector": vector_retriever}, query_engine_dict=agents, verbose=True
        )
        
        response_synthesizer = get_response_synthesizer(response_mode="compact")
        self.query_engine = RetrieverQueryEngine.from_args(
            recursive_retriever, response_synthesizer=response_synthesizer, service_context=service_context
        )


 