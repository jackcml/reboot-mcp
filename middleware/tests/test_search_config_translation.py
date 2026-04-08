import pytest                                                                                                                     
from middleware.components.search_config import SearchConfig
from middleware.graph.client import _build_graphiti_config                                                                        
from graphiti_core.search.search_config import (
      NodeReranker,                                                                                                                 
      EdgeReranker,
      NodeSearchMethod,                                                                                                             
      EdgeSearchMethod,
  )
                                                                                                                                    
   
def test_architectural_uses_node_distance_with_bfs():
    # semantic=0.3, recency=0.1, structural=0.6
    config = SearchConfig(semantic_weight=0.3, recency_weight=0.1, structural_weight=0.6)
    result = _build_graphiti_config(config, limit=10)

    assert result.node_config.reranker == NodeReranker.node_distance
    assert result.edge_config.reranker == EdgeReranker.node_distance
    assert NodeSearchMethod.bfs in result.node_config.search_methods
    assert result.limit == 10


def test_explanatory_uses_rrf_no_bfs():
    # semantic=0.6, recency=0.1, structural=0.3
    config = SearchConfig(semantic_weight=0.6, recency_weight=0.1, structural_weight=0.3)
    result = _build_graphiti_config(config, limit=10)

    assert result.node_config.reranker == NodeReranker.rrf
    assert result.edge_config.reranker == EdgeReranker.rrf
    assert NodeSearchMethod.bfs not in result.node_config.search_methods
    assert result.limit == 10                                                                                                     
   
                                                                                                                                    
def test_procedural_uses_node_distance_with_bfs():
    # semantic=0.3, recency=0.2, structural=0.5                                                                                   
    config = SearchConfig(semantic_weight=0.3, recency_weight=0.2, structural_weight=0.5)
    result = _build_graphiti_config(config, limit=10)
                                                                                                                                
    assert result.node_config.reranker == NodeReranker.node_distance
    assert result.edge_config.reranker == EdgeReranker.node_distance                                                              
    assert NodeSearchMethod.bfs in result.node_config.search_methods
    assert EdgeSearchMethod.bfs in result.edge_config.search_methods                                                              
   
                                                                                                                                    
def test_factual_uses_episode_mentions_no_bfs():
    # semantic=0.3, recency=0.5, structural=0.2
    config = SearchConfig(semantic_weight=0.3, recency_weight=0.5, structural_weight=0.2)                                         
    result = _build_graphiti_config(config, limit=10)
                                                                                                                                
    assert result.node_config.reranker == NodeReranker.episode_mentions                                                           
    assert result.edge_config.reranker == EdgeReranker.episode_mentions
    assert NodeSearchMethod.bfs not in result.node_config.search_methods                                                          
                
                                                                                                                                    
def test_sim_min_score_scales_with_semantic_weight():
    high_semantic = SearchConfig(semantic_weight=0.7, recency_weight=0.1, structural_weight=0.2)                                  
    low_semantic = SearchConfig(semantic_weight=0.3, recency_weight=0.5, structural_weight=0.2)
                                                                                                                                
    high_result = _build_graphiti_config(high_semantic, limit=10)                                                                 
    low_result = _build_graphiti_config(low_semantic, limit=10)                                                                   
                                                                                                                                
    # Higher semantic weight → lower sim_min_score (wider net)
    assert high_result.node_config.sim_min_score < low_result.node_config.sim_min_score
                                                                                                                                
   
def test_bfs_depth_scales_with_structural_weight():                                                                               
    low = SearchConfig(semantic_weight=0.7, recency_weight=0.1, structural_weight=0.2)
    high = SearchConfig(semantic_weight=0.3, recency_weight=0.2, structural_weight=0.5)                                           
                                                                                                                                
    low_result = _build_graphiti_config(low, limit=10)                                                                            
    high_result = _build_graphiti_config(high, limit=10)                                                                          
                
    assert high_result.node_config.bfs_max_depth > low_result.node_config.bfs_max_depth                                           

                                                                                                                                    
def test_none_config_path_in_search_graph_signature():
    # Verify the fallback (config=None) doesn't crash at import time                                                              
    from middleware.graph.client import search_graph                                                                              
    import inspect
    sig = inspect.signature(search_graph)                                                                                         
    assert sig.parameters["config"].default is None                                                                               

