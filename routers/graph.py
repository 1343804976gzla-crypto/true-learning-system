"""
知识图谱路由
图谱数据查询和连接管理
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from models import get_db, Chapter, ConceptMastery, ConceptLink
from schemas import GraphData, GraphNode, GraphLink, CreateLinkRequest

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/{book}", response_model=GraphData)
async def get_graph_data(
    book: str,
    db: Session = Depends(get_db)
):
    """
    获取指定书籍的知识图谱数据
    """
    # 获取该书的章节
    chapters = db.query(Chapter).filter(Chapter.book == book).all()
    
    nodes = []
    links = []
    
    for chapter in chapters:
        # 章节点
        chapter_mastery = 0.0
        concepts = db.query(ConceptMastery).filter(
            ConceptMastery.chapter_id == chapter.id
        ).all()
        
        if concepts:
            chapter_mastery = sum(c.retention for c in concepts) / len(concepts)
        
        nodes.append(GraphNode(
            id=chapter.id,
            name=chapter.chapter_title,
            chapter=chapter.chapter_title,
            mastery=chapter_mastery,
            radius=10 + chapter_mastery * 10
        ))
        
        # 知识点节点
        for concept in concepts:
            nodes.append(GraphNode(
                id=concept.concept_id,
                name=concept.name,
                chapter=chapter.chapter_title,
                mastery=concept.retention,
                radius=5 + concept.understanding * 10
            ))
            
            # 章节-知识点连接
            links.append(GraphLink(
                source=chapter.id,
                target=concept.concept_id,
                type="contains",
                strength=1.0
            ))
    
    # 获取用户自定义连接
    custom_links = db.query(ConceptLink).all()
    for link in custom_links:
        # 只添加存在的节点之间的连接
        node_ids = {n.id for n in nodes}
        if link.from_concept in node_ids and link.to_concept in node_ids:
            links.append(GraphLink(
                source=link.from_concept,
                target=link.to_concept,
                type=link.link_type,
                strength=link.strength
            ))
    
    return GraphData(nodes=nodes, links=links)


@router.post("/link")
async def create_concept_link(
    data: CreateLinkRequest,
    db: Session = Depends(get_db)
):
    """
    创建知识点之间的连接
    """
    # 检查节点是否存在
    from_concept = db.query(ConceptMastery).filter(
        ConceptMastery.concept_id == data.from_concept
    ).first()
    
    to_concept = db.query(ConceptMastery).filter(
        ConceptMastery.concept_id == data.to_concept
    ).first()
    
    if not from_concept or not to_concept:
        raise HTTPException(status_code=404, detail="知识点不存在")
    
    # 创建连接
    link = ConceptLink(
        from_concept=data.from_concept,
        to_concept=data.to_concept,
        link_type=data.link_type,
        user_created=True
    )
    
    db.add(link)
    db.commit()
    
    return {"status": "created", "message": "连接已创建"}
