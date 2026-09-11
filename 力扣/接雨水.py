# 动态规划
class Solution:
    def trap(self, height):
        n=len(height)
        leftmax=[0]*n
        rightmax=[0]*n
        for i in range(1,n):
            leftmax[i]=max(leftmax[i-1],height[i-1])
            rightmax[n-i-1]=max(rightmax[n-i],height[n-i])
        result=0
        for i in range(n):
            result+=max(0,min(leftmax[i],rightmax[i])-height[i])
        return result

if __name__=="__main__":
    n=int(input())
    height=list(map(int,input().split()))
    s=Solution()
    print(s.trap(height))