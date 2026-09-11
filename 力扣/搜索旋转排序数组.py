import random
class Solution:
    def search(self, nums, target):
        def devide(left,right):
            if left<=right:
                middle=(left+right)//2
                if nums[middle]==target:
                    return middle
                if nums[left]<=nums[middle]:
                    if nums[left]<=target<nums[middle]:
                        return devide(left,middle-1)
                    else:
                        return devide(middle+1,right)
                else:
                    if nums[middle]<target<=nums[right]:
                        return devide(middle+1,right)
                    else:
                        return devide(left,middle-1)
            else: return -1

        left=0
        n=len(nums)
        right=n-1
        return devide(left,right)

if __name__=="__main__":
    n=int(input())
    nums=list(map(int,input().split()))
    target=int(input())
    s=Solution()
    print(s.search(nums,target))