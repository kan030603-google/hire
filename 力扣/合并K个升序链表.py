# Definition for singly-linked list.
# class ListNode(object):
#     def __init__(self, val=0, next=None):
#         self.val = val
#         self.next = next
import sys
class ListNode():
    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next

def makelist(numslist):
    head=None
    lists=[]
    for nums in numslist:
        if not nums: continue
        dummy=ListNode()
        p=dummy
        for num in nums:
            node=ListNode(num)
            p.next=node
            p=p.next
        lists.append(dummy.next)
    return lists

class Solution():
    def mergetwolist(self,head1,head2):
        dummynode=ListNode()
        p=dummynode
        p1=head1
        p2=head2
        while p1 and p2:
            if p1.val>p2.val:
                p.next=p2
                p2=p2.next
                p=p.next
            else:
                p.next=p1
                p1=p1.next
                p=p.next
        if p1: p.next=p1
        if p2: p.next=p2
        return dummynode.next

    def mergeKLists(self, lists):
        """
        :type lists: List[Optional[ListNode]]
        :rtype: Optional[ListNode]
        """
        if not lists: return
        n=len(lists)
        m=n
        while m>1:
            for i in range(m//2):
                lists[i]=self.mergetwolist(lists[i],lists[(m+1)//2+i])
            m=(m+1)//2
        return lists[0]

if __name__=="__main__":
    n=int(input())
    strslist=sys.stdin.read().splitlines()
    numslist=[]
    for strs in strslist:
        if not strs: continue
        nums=list(map(int,strs.split()))
        numslist.append(nums)
    s=Solution()
    numslist=makelist(numslist)
    head=s.mergeKLists(numslist)
    p=head
    result=""
    while p:
        result+=str(p.val)+" "
        p=p.next
    print(result)