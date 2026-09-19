# Definition for a binary tree node.
import sys
class TreeNode:
    def __init__(self, val=0, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right

class Solution:
    def levelOrder(self, root):
        if not root: return []
        result=[]
        dq=[root]
        while dq:
            ndq=[]
            result.append(list(node.val for node in dq))
            for node in dq:
                if node.left:
                    ndq.append(node.left)
                if node.right:
                    ndq.append(node.right)
            dq=ndq
        return result

if __name__=="__main__":
    nodevallist=list(input().split())
    if nodevallist[0]=="null": 
        print([])
        sys.exit()
    count=len(nodevallist)
    nodelist=[]
    for nodeval in nodevallist:
        if nodeval!="null":
            nodelist.append(TreeNode(int(nodeval)))
        else:
            nodelist.append(None)
    index=1
    for node in nodelist:
        if not node: continue
        if index<count:
            node.left=nodelist[index]
            index+=1
        if index<count:
            node.right=nodelist[index]
            index+=1
    s=Solution()
    resultlist=s.levelOrder(nodelist[0])
    for result in resultlist:
        result=list(map(str,result))
        print(" ".join(result))
