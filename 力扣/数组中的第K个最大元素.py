class Solution:
    def findKthLargest(self, nums, k) :
        def devide(left,right):
            head,end=left,right
            middle=(left+right)//2
            nums[left],nums[middle]=nums[middle],nums[left]
            i=left+1
            while i<=right:
                if nums[i]<nums[left]:
                    nums[left],nums[i]=nums[i],nums[left]
                    left+=1
                    i+=1
                elif nums[i]>nums[left]:
                    nums[right],nums[i]=nums[i],nums[right]
                    right-=1
                else:
                    i+=1
            if left<=len(nums)-k<=right:
                return nums[left]
            elif len(nums)-k>right:
                return devide(right+1,end)
            else:
                return devide(head,left-1)
        
        left,right=0,len(nums)-1
        return devide(left,right) 
if __name__=="__main__":
    n,k=map(int,input().split())
    nums=list(map(int,input().split()))
    s=Solution()
    print(s.findKthLargest(nums,k))