# Definition for singly-linked list.
class ListNode(object):
    def __init__(self, x):
        self.val = x
        self.next = None

def makelist(nums):
    dummy=ListNode(0)
    p=dummy
    for num in nums:
        node=ListNode(num)
        p.next=node
        p=p.next
    return dummy.next

class Solution(object):
    def hasCycle(self, head):
        """
        :type head: ListNode
        :rtype: bool
        """
        if not head:return False
        p1,p2=head,head.next
        while p2 and p2.next:
            if p2==p1: return True
            p2=p2.next.next
            p1=p1.next
        return False

if __name__=="__main__":
    n=int(input())
    nums=list(map(int,input().split()))
    head=makelist(nums)
    pos=int(input())
    p=head
    q=head
    while p.next: p=p.next
    if pos!=-1:
        for i in range(pos):
            q=q.next
        p.next=q
    s=Solution()
    print(s.hasCycle(head))