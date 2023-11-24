# LangChain and OpenAI for conversational agents and language models
from langchain.llms import OpenAI
from langchain.chat_models import ChatOpenAI
from langchain.memory import ConversationSummaryBufferMemory
from langchain.agents import ConversationalChatAgent, AgentExecutor, initialize_agent
from langchain.schema import SystemMessage
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.agents.format_scratchpad import format_to_openai_functions
from langchain.agents.output_parsers import OpenAIFunctionsAgentOutputParser
from langchain.chains import LLMChain
from langchain.schema.runnable import RunnablePassthrough

from Tools import tools

class Agent:
    """
    A class representing a conversational agent, integrating various components 
    from LangChain and OpenAI for natural language processing and conversation handling.

    Attributes:
        chat_agent (AgentExecutor): An agent executor that handles the conversational logic.
    """
    def __init__(self):
        """
        Initializes the ConversationalAgent instance by setting up the language model,
        prompt templates, memory, and other necessary components for the conversational agent.
        """

        functions = [format_tool_to_openai_function(tool) for tool in tools]
        llm = ChatOpenAI(temperature=0, model="gpt-4-1106-preview")
        model = ChatOpenAI(temperature=0, model="gpt-4-1106-preview").bind(functions=functions)
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_message),
            MessagesPlaceholder(variable_name="chat_history"),
            ("user", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        chain = RunnablePassthrough.assign(
            agent_scratchpad=lambda x: format_to_openai_functions(x["intermediate_steps"])
        ) | prompt | model | OpenAIFunctionsAgentOutputParser()

        memory = ConversationSummaryBufferMemory(
            llm=llm,
            max_tokens=1300,
            return_messages=True,
            memory_key="chat_history",
        )
        self.chat_agent = AgentExecutor(agent=chain, memory=memory, tools=tools, verbose=True)

    def talk(self, user_input: str):
        """
        Process the user input and generate a response using the chat agent.

        Args:
            user_input (str): The input string from the user.

        Returns:
            str: The generated response from the agent.
        """
        response = self.chat_agent.invoke({'user_input': user_input}) 
        return response['output']
