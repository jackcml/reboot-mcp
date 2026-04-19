from openai import AsyncOpenAI
                                                                                                                                
from middleware.config import settings                                                                                            
from middleware.models import QueryType
                                                                                                                                
_DEBUG_SIGNALS = (
    "traceback",
    "error:",
    "exception",
    "typeerror",
    "valueerror",
    "keyerror",
    "attributeerror",                                                                                                             
    "indexerror",
    "importerror",                                                                                                                
    "nameerror",
    "runtimeerror",
    "nullpointerexception",
    "segfault",                                                                                                                   
    "assert",
    "failed",                                                                                                                     
    "crash",    
    "broken",
    "not working",
    "why is",                                                                                                                     
    "why does",
    "why isn't",                                                                                                                  
    "why doesn't",
)
                                                                                                                                
SYSTEM_PROMPT = """You are a query classifier for a code search system.
Given a user query about code, classify it as exactly one of:                                                                     
- conceptual: asks about design, architecture, purpose, or "why" questions                                                        
- procedural: asks about how to do something, step-by-step processes, or workflows                                                
- factual: asks for specific facts, definitions, locations, or "what/where" questions                                             
- debugging: asks about errors, failures, stack traces, or why something is broken                                                
                                                                                                                                
Respond with exactly one word: conceptual, procedural, factual, or debugging."""                                                  
                
                                                                                                                                
class QueryClassifier:
    def __init__(self) -> None:
        self._openai = AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
                                                                                                                                
    async def classify(self, query: str) -> QueryType:                                                                            
        # Fast path: detect debugging queries without an LLM call                                                                 
        query_lower = query.lower()                                                                                               
        if any(signal in query_lower for signal in _DEBUG_SIGNALS):                                                               
            return QueryType.debugging
                                                                                                                                
        try:    
            response = await self._openai.chat.completions.create(
                model=settings.openai_model,
                messages=[                                                                                                        
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": query},                                                                           
                ],
                max_tokens=10,
                temperature=0,
            )                                                                                                                     
            text = response.choices[0].message.content.strip().lower()
            return QueryType(text)                                                                                                
        except Exception:
            return QueryType.factual

